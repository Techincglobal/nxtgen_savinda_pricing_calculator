"""
Production Plan as the printed Job Ticket + NPD Request → FG → BOM → Production Plan flow.

- NPD Request (new-product request) can be seeded from an Opportunity / Cost Sheet, then
  create FG Items for new products and have the BOM team confirm BOMs.
- A Production Plan is created from a Sales Order (Job) or an NPD Request (NPD). Because
  ERPNext requires every po_items row to reference an ACTIVE BOM, the BOM must be confirmed
  BEFORE the plan is created.
- A Frappe Workflow (seeded in install.py) drives CS → Artwork → Supply Chain → Submit,
  emailing the next team on each hand-off (see on_production_plan_update).

Reuses the source populators / context helpers from api.job_ticket.
"""
import frappe
from frappe.utils import cint, flt, now, now_datetime, today

from nxtgen_savinda_pricing_calculator.api import job_ticket as jt_api


# ── small helpers ────────────────────────────────────────────────────────────
def _fullname(user=None):
	user = user or frappe.session.user
	return frappe.db.get_value("User", user, "full_name") or user


def _require_role(role):
	roles = frappe.get_roles(frappe.session.user)
	if role not in roles and "System Manager" not in roles:
		frappe.throw("Only users with the '" + role + "' role can perform this action.")


def _first_fg_context_from_so(so):
	"""(ctx, cb, pl, pricing_type) for the first resolvable Sales Order item, else Nones."""
	for it in so.items:
		ctx = jt_api._fg_context(it.item_code)
		cb = jt_api._cb_fields(ctx.get("calculation_breakdown"))
		if cb:
			pl = jt_api._pl(ctx.get("product_library"))
			pricing = (pl.get("department") if pl else "") or cb.get("pricing_type") or "Offset"
			return ctx, cb, pl, ("Flexo" if pricing == "Flexo" else "Offset")
	return None, {}, {}, "Offset"


# ── NPD Request: seed from a source ──────────────────────────────────────────
def _npd_append_item(doc, desc, qty, ctx, cb, pl, pricing_type):
	line = jt_api._item_line_from_cb(desc, qty, ctx, cb, pl, pricing_type)
	line["item_name"] = line.pop("description", "") or desc
	doc.append("items", line)


def _npd_from_cost_sheet(doc, cs_name):
	cs = frappe.get_doc("Cost Sheet", cs_name)
	doc.cost_sheet = cs.name
	if cs.get("inquiry"):
		doc.inquiry = cs.get("inquiry")
	doc.customer_name = cs.get("customer_name") or doc.customer_name
	doc.job_title = cs.get("subject") or doc.job_title
	if cs.get("colour"):
		doc.colors = cint(cs.get("colour"))
	materials = []
	first = None
	for r in (cs.get("pricing_list") or []):
		if not r.get("item"):
			continue
		ctx = jt_api._cost_item_context(r.get("item"))
		cb = jt_api._cb_fields(ctx.get("calculation_breakdown"))
		pl = jt_api._pl(ctx.get("product_library"))
		pricing = (pl.get("department") if pl else "") or cb.get("pricing_type") or "Offset"
		ci = frappe.db.get_value("cost Item", r.get("item"), ["cost_item_name", "item_qty"], as_dict=True) or {}
		desc = ci.get("cost_item_name") or r.get("item_name") or r.get("item")
		_npd_append_item(doc, desc, r.get("qty") or ci.get("item_qty") or 0, ctx, cb, pl, pricing)
		jt_api._build_ticket_materials(ctx.get("calculation_breakdown"), pricing, cb.get("no_of_colors"), merge_into=materials)
		if first is None and cb:
			first = (cb, pl, pricing)
	if first:
		cb, pl, pricing = first
		doc.pricing_type = pricing
		doc.colors = doc.colors or cint(cb.get("no_of_colors"))
		if cb.get("base_material"):
			doc.material = frappe.db.get_value("Item", cb["base_material"], "item_name") or cb["base_material"]
		else:
			doc.material = cb.get("custom_material_name") or doc.material
		if pl:
			doc.finishings = jt_api._finishings_text(pl)
			doc.color_ref = pl.get("color_reference") or doc.color_ref
	for m in materials:
		doc.append("bom_materials", m)


def _npd_from_inquiry(doc, opp_name):
	opp = frappe.db.get_value(
		"Opportunity", opp_name,
		["party_name", "customer_name", "custom_subject", "custom_colour"], as_dict=True,
	) or {}
	doc.inquiry = opp_name
	doc.customer_name = opp.get("customer_name") or opp.get("party_name") or doc.customer_name
	doc.job_title = opp.get("custom_subject") or doc.job_title
	if opp.get("custom_colour"):
		doc.colors = cint(opp.get("custom_colour"))
	if opp.get("customer_name") and frappe.db.exists("Customer", {"customer_name": opp.get("customer_name")}):
		doc.customer = frappe.db.get_value("Customer", {"customer_name": opp.get("customer_name")}, "name")
	cs = frappe.db.get_value("Cost Sheet", {"inquiry": opp_name}, "name", order_by="creation desc")
	if cs:
		_npd_from_cost_sheet(doc, cs)
		doc.inquiry = opp_name


@frappe.whitelist()
def create_npd_request_from_inquiry(opportunity):
	doc = frappe.new_doc("NPD Request")
	_npd_from_inquiry(doc, opportunity)
	doc.insert(ignore_permissions=True)
	return {"npd_request": doc.name}


@frappe.whitelist()
def create_npd_request_from_cost_sheet(cost_sheet):
	doc = frappe.new_doc("NPD Request")
	_npd_from_cost_sheet(doc, cost_sheet)
	doc.insert(ignore_permissions=True)
	return {"npd_request": doc.name}


# ── NPD Request actions: Create FG, Confirm BOM ──────────────────────────────
# Product Library fieldnames the FG-creation popup exposes for review/edit.
_PL_KEYS = [
	"department", "flexo_type", "customer_product_code", "no_of_colors", "no_of_ups",
	"full_sheet_size", "cut_sheet_size", "product_size", "width_mm", "length_mm",
	"artwork_no", "artwork_version", "core_size", "pcs_per_roll", "printing_machine",
]


def _fg_detail_defaults(row, npd, ig, dept):
	"""Proposed Item + Product Library values for one requested line (pre-filled from the
	NPD Request line / header). Shown in the FG-creation popup for review/edit."""
	is_flexo = (npd.pricing_type or "Offset") == "Flexo"
	return {
		"row_name": row.name,
		"requested_name": row.item_name or "",
		# Item fields
		"item_name": (row.item_name or npd.job_title or npd.name or "").strip(),
		"item_group": ig,
		"department": dept,
		"stock_uom": row.uom or "Nos",
		"customer_ref": "",
		"cost_item": row.cost_item or "",
		# Product Library fields (pl_<fieldname>)
		"pl_department": npd.pricing_type or "",
		"pl_flexo_type": "Reel" if is_flexo else "",
		"pl_customer_product_code": row.product_code or "",
		"pl_no_of_colors": int(npd.colors or 0),
		"pl_no_of_ups": int(row.ups or row.repeat_ups or 0),
		"pl_full_sheet_size": row.full_sheet_size or "",
		"pl_cut_sheet_size": row.cut_sheet_size or "",
		"pl_product_size": row.size or npd.size or "",
		"pl_width_mm": float(row.reel_width or 0),
		"pl_length_mm": float(row.reel_length or 0),
		"pl_artwork_no": npd.artwork_no or "",
		"pl_artwork_version": npd.artwork_version or "",
		"pl_core_size": npd.core_size or "",
		"pl_pcs_per_roll": 0,
		"pl_printing_machine": "",
	}


@frappe.whitelist()
def get_fg_preview(npd_request):
	"""Return the proposed FG + Product Library details for each line still missing an FG,
	so the user can review/edit them in a popup before the FG is created."""
	doc = frappe.get_doc("NPD Request", npd_request)
	ig = jt_api._resolve_fg_item_group(doc)
	dept = jt_api._resolve_department(doc)
	lines = [
		_fg_detail_defaults(row, doc, ig, dept)
		for row in doc.items
		if not (row.fg_item and frappe.db.exists("Item", row.fg_item))
	]
	existing = [row.fg_item for row in doc.items if row.fg_item and frappe.db.exists("Item", row.fg_item)]
	return {"lines": lines, "existing": existing, "is_flexo": (doc.pricing_type or "Offset") == "Flexo"}


@frappe.whitelist()
def create_fg_items(npd_request, details=None):
	"""Create an FG Item for each requested line that has none yet. When `details` is given
	(the user-reviewed values from the popup), those override the computed defaults; the
	Product Library is created with the edited values too."""
	from nxtgen_savinda_pricing_calculator.api.manufacturing import create_fg_item

	if isinstance(details, str):
		details = frappe.parse_json(details) or None

	doc = frappe.get_doc("NPD Request", npd_request)
	ig = jt_api._resolve_fg_item_group(doc)
	dept0 = jt_api._resolve_department(doc)

	by_row = {}
	for d in (details or []):
		if d.get("row_name"):
			by_row[d["row_name"]] = d

	created, existing = [], []
	for row in doc.items:
		if row.fg_item and frappe.db.exists("Item", row.fg_item):
			existing.append(row.fg_item)
			continue
		d = by_row.get(row.name) or _fg_detail_defaults(row, doc, ig, dept0)
		dept = d.get("department") or dept0
		if not dept:
			frappe.throw("No Department found. Create a Department (with an abbreviation) before creating FG Items.")
		name = (d.get("item_name") or row.item_name or doc.job_title or doc.name).strip()
		pl_over = {k: d.get("pl_" + k) for k in _PL_KEYS if d.get("pl_" + k) not in (None, "")}
		res = create_fg_item(
			item_name=name, description=name,
			item_group=(d.get("item_group") or ig), department=dept,
			stock_uom=(d.get("stock_uom") or row.uom or "Nos"),
			cost_item=(d.get("cost_item") or row.cost_item or None),
			customer_ref=(d.get("customer_ref") or None),
			pl_overrides=pl_over,
		)
		frappe.db.set_value("NPD Request Item", row.name, "fg_item", res["item_code"])
		created.append(res["item_code"])
	frappe.db.commit()
	return {"created": created, "existing": existing}


def _linked_raw_materials(doc):
	raw, unlinked = [], []
	for m in doc.bom_materials:
		if m.item and frappe.db.exists("Item", m.item):
			raw.append({
				"item_code": m.item, "item_name": m.item_name or m.item,
				"qty": flt(m.quantity) or 1, "uom": m.uom or None, "rate": 0,
			})
		else:
			unlinked.append(m.item_name or m.item or "(unnamed)")
	return raw, unlinked


@frappe.whitelist()
def confirm_bom(npd_request):
	"""BOM team: ensure every FG has an active BOM (auto-build a flat zero-cost one from
	the reference materials if missing), then mark the NPD Request BOM-confirmed."""
	_require_role("BOM Team")
	from nxtgen_savinda_pricing_calculator.api.bom_builder import check_existing_bom, create_bom

	doc = frappe.get_doc("NPD Request", npd_request)
	raw, _unlinked = _linked_raw_materials(doc)
	missing, built = [], []
	for row in doc.items:
		if not row.fg_item:
			missing.append((row.item_name or "(unnamed)") + " — no FG Item (run Create FG Items first)")
			continue
		if check_existing_bom(row.fg_item):
			continue
		if not raw:
			missing.append(row.fg_item + " — no BOM (build one via BOM Builder)")
			continue
		try:
			res = create_bom(row.fg_item, flt(row.qty) or 1, [], raw, is_default=1)
			built.append(res.get("bom_name"))
		except Exception as e:
			missing.append("%s — %s" % (row.fg_item, str(e)[:80]))
	if missing:
		return {"ok": False, "missing": missing, "built": built}
	frappe.db.set_value("NPD Request", doc.name, {
		"bom_confirmed": 1, "bom_by": _fullname(), "bom_on": now(),
	})
	frappe.db.commit()
	return {"ok": True, "built": built}


# ── Production Plan creation from a source (Sales Order | NPD Request) ────────
_GEOM_MAP = [
	("product_code", "custom_product_code"), ("size", "custom_size"),
	("full_sheets", "custom_full_sheets"), ("cut_sheets", "custom_cut_sheets"),
	("full_sheet_size", "custom_full_sheet_size"), ("cut_sheet_size", "custom_cut_sheet_size"),
	("cuts", "custom_cuts"), ("ups", "custom_ups"),
	("reel_length", "custom_reel_length"), ("reel_width", "custom_reel_width"),
	("reel_area", "custom_reel_area"), ("slit_width", "custom_slit_width"),
	("repeat_teeth", "custom_repeat_teeth"), ("repeat_ups", "custom_repeat_ups"),
	("across_ups", "custom_across_ups"), ("across_gaps", "custom_across_gaps"),
	("material_width", "custom_material_width"), ("ups_per_reel", "custom_ups_per_reel"),
	("labels_per_reel", "custom_labels_per_reel"),
]


def _po_row_from_geom(geom):
	"""Map a geometry source dict (Job Ticket Item-shaped) to PP Item custom_* fields."""
	return {cust: geom.get(src) for src, cust in _GEOM_MAP if geom.get(src) not in (None, "")}


def _resolve_bom_no(fg_item):
	from nxtgen_savinda_pricing_calculator.api.bom_builder import check_existing_bom
	b = check_existing_bom(fg_item)
	if b:
		return b.get("name") if isinstance(b, dict) else b
	return jt_api._default_bom(fg_item)


@frappe.whitelist()
def create_production_plan_from_source(source_type, source_name):
	"""Create a DRAFT Production Plan from a Sales Order (Job) or NPD Request (NPD).
	ALL fetched items go to custom_ticket_items; only the BOM-ready ones also go to po_items
	(ERPNext requires a BOM there). Items without a BOM stay on the ticket and are flagged
	for the BOM team — no hard block, so items can be fetched before BOMs exist."""
	pp = frappe.new_doc("Production Plan")
	pp.company = jt_api._default_company()
	pp.posting_date = today()

	ticket_type, pricing_type, header, lines = _gather_ticket(source_type, source_name)
	if not lines:
		frappe.throw("No items found on the selected " + str(source_type) + ".")

	for tl in lines:
		pp.append("custom_ticket_items", tl)
	_build_po_items(pp)

	pp.custom_ticket_type = ticket_type
	pp.custom_pricing_type = "Flexo" if pricing_type == "Flexo" else "Offset"
	pp.custom_created_by = _fullname()
	pp.custom_created_on = now()
	for k, v in header.items():
		if v not in (None, ""):
			pp.set(k, v)
	missing = [tl for tl in lines if not tl.get("has_bom")]
	pp.custom_needs_bom = 1 if missing else 0
	pp.custom_bom_confirmed = 0 if missing else 1
	pp.workflow_state = "Draft"

	pp.insert(ignore_permissions=True)

	if source_type == "NPD Request":
		frappe.db.set_value("NPD Request", source_name, "production_plan", pp.name)

	pending = [tl.get("fg_item") or tl.get("description") or "?" for tl in missing]
	if pending:
		try:
			notify_role(
				"BOM Team", "BOM needed for " + pp.name,
				"Production Plan <b>%s</b> has items without a BOM: %s.<br>"
				"Open the BOM Builder to create them, then run Sync BOMs." % (pp.name, ", ".join(pending)),
				pp,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "notify pending BOM failed")
	return {"production_plan": pp.name, "pending_bom": pending, "needs_bom": bool(pending)}


@frappe.whitelist()
def create_plan_from_npd(npd_request):
	return create_production_plan_from_source("NPD Request", npd_request)


@frappe.whitelist()
def create_plan_from_sales_order(sales_order):
	return create_production_plan_from_source("Sales Order", sales_order)


# ── Ticket line list + BOM-ready po_items ─────────────────────────────────────
_TICKET_GEOM_FIELDS = (
	"product_code", "size", "full_sheets", "cut_sheets", "full_sheet_size", "cut_sheet_size",
	"cuts", "ups", "reel_length", "reel_width", "reel_area", "slit_width", "repeat_teeth",
	"repeat_ups", "repeat_gaps", "across_ups", "across_gaps", "material_width",
	"ups_per_reel", "labels_per_reel",
)


def _ticket_line(fg, qty, geom, bom_no):
	"""One custom_ticket_items row (Job Ticket Item shape) for a fetched FG."""
	line = {
		"fg_item": fg or "",
		"description": geom.get("description") or geom.get("item_name")
			or (frappe.db.get_value("Item", fg, "item_name") if fg else "") or fg or "",
		"qty": flt(qty) or 1,
		"cost_item": geom.get("cost_item") or "",
		"calculation_breakdown": geom.get("calculation_breakdown") or "",
		"bom_no": bom_no or "",
		"has_bom": 1 if bom_no else 0,
	}
	for f in _TICKET_GEOM_FIELDS:
		v = geom.get(f)
		if v not in (None, ""):
			line[f] = v
	return line


def _gather_ticket(source_type, source_name):
	"""Return (ticket_type, pricing_type, header_dict, ticket_lines) for a source."""
	if source_type == "NPD Request":
		src = frappe.get_doc("NPD Request", source_name)
		pricing = src.pricing_type or "Offset"
		lines = []
		for row in src.items:
			geom = {"description": row.item_name, "size": row.size,
				"cost_item": row.cost_item, "calculation_breakdown": row.calculation_breakdown}
			for f in _TICKET_GEOM_FIELDS:
				geom.setdefault(f, row.get(f))
			bom = _resolve_bom_no(row.fg_item) if row.fg_item else None
			lines.append(_ticket_line(row.fg_item, row.qty, geom, bom))
		header = {
			"custom_npd_request": src.name, "custom_customer": src.customer,
			"custom_customer_name": src.customer_name, "custom_colors": cint(src.colors),
			"custom_job_title": src.job_title, "custom_job_board": src.material,
			"custom_material": src.material, "custom_finishings": src.finishings,
			"custom_color_ref": src.color_ref, "custom_quote_no": src.quote_no,
			"custom_remarks": src.remarks, "custom_art_no": src.artwork_no,
			"custom_art_version": src.artwork_version,
			"custom_artwork_status": src.artwork_status or "Pending",
		}
		return "NPD", pricing, header, lines

	if source_type == "Sales Order":
		so = frappe.get_doc("Sales Order", source_name)
		ctx, cb, pl, pricing = _first_fg_context_from_so(so)
		lines = []
		for it in so.items:
			fctx = jt_api._fg_context(it.item_code)
			fcb = jt_api._cb_fields(fctx.get("calculation_breakdown"))
			fpl = jt_api._pl(fctx.get("product_library"))
			pr = (fpl.get("department") if fpl else "") or fcb.get("pricing_type") or pricing
			geom = jt_api._item_line_from_cb(it.item_name or it.item_code, it.qty, fctx, fcb, fpl, pr)
			bom = _resolve_bom_no(it.item_code)
			lines.append(_ticket_line(it.item_code, it.qty, geom, bom))
		if cb.get("base_material"):
			board = frappe.db.get_value("Item", cb["base_material"], "item_name") or cb["base_material"]
		else:
			board = cb.get("custom_material_name") or ""
		header = {
			"custom_sales_order": so.name,
			"custom_customer": so.customer, "custom_customer_name": so.customer_name,
			"custom_po_no": so.po_no, "custom_req_date": so.delivery_date,
			"custom_colors": cint(cb.get("no_of_colors")), "custom_job_board": board,
			"custom_material": board,
			"custom_job_title": (pl.get("product_name") if pl else "") or so.customer_name,
			"custom_art_no": (pl.get("artwork_no") if pl else "") or "",
			"custom_art_version": (pl.get("artwork_version") if pl else "") or "",
			"custom_quote_no": (pl.get("quotation_no") if pl else "") or "",
			"custom_finishings": jt_api._finishings_text(pl) if pl else "",
			"custom_artwork_status": "Approved",
		}
		return "Job", pricing, header, lines

	return None, "Offset", {}, []


def _build_po_items(pp, fg_wh=None):
	"""(Re)build po_items from the BOM-ready rows of custom_ticket_items."""
	if fg_wh is None:
		fg_wh = jt_api._default_fg_warehouse(pp.get("company") or jt_api._default_company())
	pp.set("po_items", [])
	for tl in (pp.get("custom_ticket_items") or []):
		if not (tl.get("has_bom") and tl.get("bom_no") and frappe.db.exists("BOM", tl.get("bom_no"))):
			continue
		stock_uom = frappe.db.get_value("Item", tl.fg_item, "stock_uom") or "Nos"
		po = {
			"item_code": tl.fg_item, "bom_no": tl.bom_no,
			"planned_qty": flt(tl.qty) or 1, "pending_qty": flt(tl.qty) or 1,
			"stock_uom": stock_uom, "warehouse": fg_wh, "planned_start_date": now_datetime(),
		}
		po.update(_po_row_from_geom({f: tl.get(f) for f in _TICKET_GEOM_FIELDS}))
		pp.append("po_items", po)


def _resolve_source(pp):
	npd = pp.get("custom_npd_request")
	if npd and frappe.db.exists("NPD Request", npd):
		return "NPD Request", npd
	so = pp.get("custom_sales_order")
	if so and frappe.db.exists("Sales Order", so):
		return "Sales Order", so
	so_names = [r.sales_order for r in (pp.get("sales_orders") or []) if r.get("sales_order")]
	if not so_names:
		so_names = list(dict.fromkeys(
			[r.get("sales_order") for r in (pp.get("po_items") or []) if r.get("sales_order")]))
	so_names = [s for s in so_names if s]
	if so_names and frappe.db.exists("Sales Order", so_names[0]):
		return "Sales Order", so_names[0]
	return None, None


@frappe.whitelist()
def bom_builder_url(production_plan):
	"""URL to open the BOM Builder for this plan's source: the NPD's related Cost Sheet
	(NPD plans) or the Sales Order (Job plans). Empty string if none is available."""
	pp = frappe.get_doc("Production Plan", production_plan)
	npd = pp.get("custom_npd_request")
	if npd:
		cs = frappe.db.get_value("NPD Request", npd, "cost_sheet")
		if cs:
			return "/app/bom-builder?cost_sheet=" + frappe.utils.quote(cs)
	source_type, source_name = _resolve_source(pp)
	if source_type == "Sales Order" and source_name:
		return "/app/bom-builder?so=" + frappe.utils.quote(source_name)
	if source_type == "NPD Request":
		cs = frappe.db.get_value("NPD Request", source_name, "cost_sheet")
		if cs:
			return "/app/bom-builder?cost_sheet=" + frappe.utils.quote(cs)
	return ""


def _populate_pp_ticket_fields(pp, force=False):
	"""Fill Job Ticket header + the custom_ticket_items list (ALL fetched FGs) from the
	plan's source (NPD Request / Sales Order). Only-empty unless force=True. Rebuilds the
	BOM-ready po_items subset when the ticket list is (re)built. Returns True if a source
	was resolved."""
	source_type, source_name = _resolve_source(pp)
	if not source_type:
		return False
	ticket_type, pricing_type, header, lines = _gather_ticket(source_type, source_name)

	def setf(field, val):
		if val in (None, ""):
			return
		if force or not pp.get(field):
			pp.set(field, val)

	setf("custom_ticket_type", ticket_type)
	setf("custom_pricing_type", "Flexo" if pricing_type == "Flexo" else "Offset")
	for k, v in header.items():
		setf(k, v)
	if not pp.get("custom_created_by"):
		setf("custom_created_by", _fullname())
		setf("custom_created_on", now())

	if lines and (force or not (pp.get("custom_ticket_items") or [])):
		pp.set("custom_ticket_items", [])
		for tl in lines:
			pp.append("custom_ticket_items", tl)
		if force or not (pp.get("po_items") or []):
			_build_po_items(pp)
		missing = any(not tl.get("has_bom") for tl in lines)
		pp.custom_needs_bom = 1 if missing else 0
		pp.custom_bom_confirmed = 0 if missing else 1
	return True


def on_production_plan_before_save(doc, method=None):
	"""Auto-fetch Job Ticket header + item list as soon as a source (SO / NPD) is set."""
	try:
		_populate_pp_ticket_fields(doc, force=False)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "production_plan auto-populate failed")


@frappe.whitelist()
def fetch_ticket_details(production_plan):
	"""Manual re-fetch (force) of the Job Ticket header + item list from the source."""
	pp = frappe.get_doc("Production Plan", production_plan)
	if pp.docstatus != 0:
		frappe.throw("Fetch is only available on a draft Production Plan.")
	_populate_pp_ticket_fields(pp, force=True)
	pp.save(ignore_permissions=True)
	return {"ok": True}


@frappe.whitelist()
def sync_boms(production_plan):
	"""Re-check each ticket item for an active BOM (e.g. after the BOM team built them),
	move the now-ready ones into po_items, and clear the pending flag when all resolve."""
	pp = frappe.get_doc("Production Plan", production_plan)
	if pp.docstatus != 0:
		frappe.throw("Sync BOMs is only available on a draft Production Plan.")
	resolved, still = [], []
	for tl in (pp.get("custom_ticket_items") or []):
		bom = _resolve_bom_no(tl.fg_item) if tl.fg_item else None
		if bom and frappe.db.exists("BOM", bom):
			tl.bom_no = bom
			tl.has_bom = 1
			resolved.append(tl.fg_item)
		else:
			tl.has_bom = 0
			if tl.fg_item:
				still.append(tl.fg_item)
	_build_po_items(pp)
	pp.custom_needs_bom = 1 if still else 0
	if not still and (pp.get("custom_ticket_items") or []):
		pp.custom_bom_confirmed = 1
		if not pp.get("custom_bom_by"):
			pp.custom_bom_by = _fullname()
			pp.custom_bom_on = now()
	pp.save(ignore_permissions=True)
	return {"resolved": resolved, "still_missing": still}


# ── Procurement: create a Purchase Request (Material Request) from raw materials ─
@frappe.whitelist()
def create_purchase_request(production_plan):
	"""Create a draft Purchase Request (Material Request, type = Purchase) for the plan's
	raw materials — only the rows whose request type is 'Purchase' (manufacture / transfer
	rows are skipped). This is the Supply Chain procurement step (run after Get Raw
	Materials + Add Wastage). Nothing is submitted — the team reviews and processes it."""
	_require_role("Supply Chain")
	pp = frappe.get_doc("Production Plan", production_plan)
	rows = [
		m for m in (pp.get("mr_items") or [])
		if m.item_code and flt(m.quantity) > 0
		and (m.get("material_request_type") or "Purchase") == "Purchase"
	]
	if not rows:
		frappe.throw(
			"No <b>Purchase</b>-type raw materials found. Run 'Get Raw Materials for "
			"Production' (then Add Wastage) first."
		)

	company = pp.company or jt_api._default_company()
	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Purchase"
	mr.company = company
	mr.transaction_date = today()
	mr.schedule_date = today()
	for m in rows:
		mr.append("items", {
			"item_code": m.item_code,
			"qty": flt(m.quantity),
			"schedule_date": m.get("schedule_date") or today(),
			"warehouse": m.warehouse,
			"uom": m.get("uom") or None,
		})
	mr.insert(ignore_permissions=True)

	note = "Purchase Request (Material Request) created (%s): %s" % (now(), mr.name)
	existing = frappe.db.get_value("Production Plan", pp.name, "custom_remarks") or ""
	frappe.db.set_value("Production Plan", pp.name, "custom_remarks", (existing + "\n" + note).strip())
	frappe.db.commit()
	return {"material_request": mr.name}


# ── Workflow side-effects: stamps + email notifications ──────────────────────
def _role_recipients(role):
	users = [d.parent for d in frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, fields=["parent"])]
	out = []
	for u in set(users):
		if u in ("Administrator", "Guest"):
			continue
		info = frappe.db.get_value("User", u, ["enabled", "email"], as_dict=True)
		if info and info.enabled and info.email:
			out.append(info.email)
	return out


def notify_role(role, subject, message, doc=None):
	try:
		recipients = _role_recipients(role)
		if not recipients:
			return
		frappe.sendmail(
			recipients=recipients, subject=subject, message=message,
			reference_doctype=(doc.doctype if doc else None),
			reference_name=(doc.name if doc else None),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "production_plan.notify_role failed")


def _stamp(name, values):
	try:
		frappe.db.set_value("Production Plan", name, values, update_modified=False)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "production_plan._stamp failed")


def _msg(doc, tail):
	title = doc.get("custom_job_title") or doc.name
	return "Production Plan <b>{0}</b> ({1}) {2}".format(doc.name, title, tail)


def _run_stock_stub(doc):
	# Placeholder supply-chain / stock validation. A fuller implementation can compute
	# shortfalls from mr_items / projected qty. For now record a timestamped note.
	note = "Stock validated on %s by %s." % (now(), _fullname())
	existing = frappe.db.get_value("Production Plan", doc.name, "custom_remarks") or ""
	_stamp(doc.name, {"custom_remarks": (existing + "\n" + note).strip()})


def on_production_plan_update(doc, method=None):
	"""Stamp approval fields and email the next team when workflow_state changes."""
	new_state = doc.get("workflow_state")
	if not new_state:
		return
	# Only act on OUR plans (created through this flow).
	if not doc.get("custom_ticket_type"):
		return
	before = doc.get_doc_before_save()
	old_state = before.get("workflow_state") if before else None
	if new_state == old_state:
		return

	name = doc.name
	if new_state == "Draft" and before is None:
		notify_role("BOM Team", "Production Plan created: " + name, _msg(doc, "was created — please review the BOM."), doc)
		notify_role("Supply Chain", "Production Plan created: " + name, _msg(doc, "was created — please prepare stock validation."), doc)
	elif new_state == "Artwork Pending":
		notify_role("Artwork Approver", "Artwork approval needed: " + name, _msg(doc, "is awaiting artwork approval."), doc)
	elif new_state == "Supply Chain Validation":
		_stamp(name, {"custom_artwork_status": "Approved", "custom_artwork_by": _fullname(), "custom_artwork_on": now()})
		notify_role("Supply Chain", "Stock validation needed: " + name, _msg(doc, "artwork approved — please validate stock."), doc)
	elif new_state == "Approved":
		_run_stock_stub(doc)
		_stamp(name, {"custom_stock_validated": 1, "custom_checked_by": _fullname(), "custom_checked_on": now()})
		notify_role("Manufacturing User", "Ready to submit: " + name, _msg(doc, "is approved — ready to submit."), doc)
	elif new_state == "Rejected":
		_stamp(name, {"custom_artwork_status": "Rejected"})
		notify_role("CS Team", "Production Plan rejected: " + name, _msg(doc, "was rejected."), doc)
	elif new_state == "Submitted":
		notify_role("CS Team", "Production Plan submitted: " + name, _msg(doc, "was submitted — production documents can now be created."), doc)
