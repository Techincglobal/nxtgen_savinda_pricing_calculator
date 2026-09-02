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
import json

import frappe
from frappe.utils import add_days, cint, flt, now, now_datetime, today

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


# ── NPD Request: seed from a source (LIGHT — header only; FGs live on the Cost Sheet) ──
def _npd_from_cost_sheet(doc, cs_name):
	cs = frappe.get_doc("Cost Sheet", cs_name)
	doc.cost_sheet = cs.name
	if cs.get("inquiry"):
		doc.inquiry = cs.get("inquiry")
	doc.customer_name = cs.get("customer_name") or doc.customer_name
	doc.job_title = cs.get("subject") or doc.job_title
	if cs.get("colour"):
		doc.colors = cint(cs.get("colour"))
	# Pricing type + a few display fields from the first cost item's Calculation Breakdown.
	for r in (cs.get("pricing_list") or []):
		if not r.get("item"):
			continue
		ctx = jt_api._cost_item_context(r.get("item"))
		cb = jt_api._cb_fields(ctx.get("calculation_breakdown"))
		if not cb:
			continue
		pl = jt_api._pl(ctx.get("product_library"))
		doc.pricing_type = (pl.get("department") if pl else "") or cb.get("pricing_type") or "Offset"
		doc.colors = doc.colors or cint(cb.get("no_of_colors"))
		if cb.get("base_material"):
			doc.material = frappe.db.get_value("Item", cb["base_material"], "item_name") or cb["base_material"]
		else:
			doc.material = cb.get("custom_material_name") or doc.material
		if pl:
			doc.finishings = jt_api._finishings_text(pl)
			doc.color_ref = pl.get("color_reference") or doc.color_ref
		break


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
# Product Library fields exposed in the FG-creation popup for review/edit, in display
# order. `only` restricts a field to a pricing type: "" = both, else "Offset" / "Flexo".
# Labels / fieldtypes / options come from the Product Library meta (see _pl_field_schema),
# so this stays a single source of truth for both popups.
_PL_REVIEW = [
	("customer_product_code", ""), ("category", ""), ("department", ""),
	("artwork_no", ""), ("artwork_version", ""),
	("full_sheet_size", "Offset"), ("cut_sheet_size", "Offset"),
	("product_size", ""), ("pasting_type", "Offset"),
	("no_of_colors", ""), ("no_of_ups", ""),
	("proof_standard", "Offset"), ("printing_machine", "Offset"),
	("flexo_type", "Flexo"), ("width_mm", "Flexo"), ("length_mm", "Flexo"),
	("core_size", "Flexo"), ("pcs_per_roll", "Flexo"),
	("winding_direction", "Flexo"), ("tolerance", "Flexo"), ("remark", ""),
	("cold_foil", ""), ("hot_foil", ""), ("embossing", ""),
	("lamination", ""),("die_cut_code", "")
]
_PL_KEYS = [fn for fn, _ in _PL_REVIEW]


def _pl_field_schema():
	"""Render metadata for the reviewable Product Library fields, driven off the doctype
	meta so the two FG-creation popups render identical field sets without duplicating
	labels/options in JS."""
	meta = frappe.get_meta("Product Library")
	out = []
	for fn, only in _PL_REVIEW:
		df = meta.get_field(fn)
		if not df:
			continue
		out.append({
			"fieldname": fn, "label": df.label or fn,
			"fieldtype": df.fieldtype, "options": df.options or "", "only": only,
		})
	return out


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
	return {
		"lines": lines, "existing": existing,
		"is_flexo": (doc.pricing_type or "Offset") == "Flexo",
		"pl_fields": _pl_field_schema(),
	}


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


# ── Create FG on the Cost Sheet (for the light NPD flow: FGs are made here first) ─
def _dept_for_pricing(pricing):
	return (
		frappe.db.get_value("Department", {"department_name": pricing}, "name")
		or frappe.db.get_value("Department", {"is_group": 0}, "name")
		or frappe.db.get_value("Department", {}, "name")
	)


def _fg_item_group_default(cost_sheet):
	return (
		frappe.db.get_value("Cost Sheet", cost_sheet, "item_group")
		or frappe.db.get_value("Item Group", {"item_group_name": "Finished Goods"}, "name")
		or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
	)


def _cost_sheet_fg_defaults(ci, cost_sheet, ig):
	"""Proposed Item + Product Library values for one Cost Sheet cost item."""
	ci_doc = frappe.db.get_value(
		"cost Item", ci, ["cost_item_name", "colour", "material"], as_dict=True) or {}
	cb = frappe.db.get_value(
		"Cost Item Calculation", {"parent": ci}, "calculation_breakdown", order_by="idx asc")
	cbd = jt_api._cb_fields(cb) if cb else {}
	pricing = (cbd.get("pricing_type") or "Offset")
	is_flexo = pricing == "Flexo"
	return {
		"cost_item": ci,
		"item_name": ci_doc.get("cost_item_name") or ci,
		"item_group": ig,
		"department": _dept_for_pricing(pricing),
		"stock_uom": "Nos",
		"customer_ref": "",
		"pl_department": pricing,
		"pl_flexo_type": "Reel" if is_flexo else "",
		"pl_customer_product_code": "",
		"pl_no_of_colors": cint(cbd.get("no_of_colors") or ci_doc.get("colour") or 0),
		"pl_no_of_ups": cint(cbd.get("no_of_ups") or 0),
		"pl_full_sheet_size": jt_api._size_str(cbd.get("full_sheet_l"), cbd.get("full_sheet_w")),
		"pl_cut_sheet_size": jt_api._size_str(cbd.get("cut_sheet_l"), cbd.get("cut_sheetw")),
		"pl_product_size": "",
		"pl_width_mm": flt(cbd.get("_reel_width")),
		"pl_length_mm": flt(cbd.get("_reel_length")),
		"pl_artwork_no": frappe.db.get_value("Cost Sheet", cost_sheet, "artwork_no") or "",
		"pl_artwork_version": frappe.db.get_value("Cost Sheet", cost_sheet, "artwork_version") or "",
	}


@frappe.whitelist()
def get_cost_sheet_fg_preview(cost_sheet):
	"""Proposed FG + Product Library details for each Cost Sheet cost item that has no FG."""
	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	ig = _fg_item_group_default(cost_sheet)
	lines, existing = [], []
	seen = set()
	is_flexo = False
	for row in (cs.get("pricing_list") or []):
		ci = row.get("item")
		if not ci or ci in seen:
			continue
		seen.add(ci)
		fg = frappe.db.get_value("Item", {"custom_cost_item": ci}, "name")
		if fg:
			existing.append(fg)
			continue
		d = _cost_sheet_fg_defaults(ci, cost_sheet, ig)
		if d.get("pl_department") == "Flexo":
			is_flexo = True
		lines.append(d)
	return {"lines": lines, "existing": existing, "is_flexo": is_flexo, "pl_fields": _pl_field_schema()}


@frappe.whitelist()
def create_fg_from_cost_sheet(cost_sheet, details=None):
	"""Create an FG Item for each Cost Sheet cost item that has none yet (light NPD flow)."""
	from nxtgen_savinda_pricing_calculator.api.manufacturing import create_fg_item

	if isinstance(details, str):
		details = frappe.parse_json(details) or []
	by_ci = {d.get("cost_item"): d for d in (details or []) if d.get("cost_item")}

	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	ig_default = _fg_item_group_default(cost_sheet)
	created, existing = [], []
	seen = set()
	for row in (cs.get("pricing_list") or []):
		ci = row.get("item")
		if not ci or ci in seen:
			continue
		seen.add(ci)
		fg = frappe.db.get_value("Item", {"custom_cost_item": ci}, "name")
		if fg:
			existing.append(fg)
			continue
		d = by_ci.get(ci) or _cost_sheet_fg_defaults(ci, cost_sheet, ig_default)
		dept = d.get("department") or _dept_for_pricing("Offset")
		if not dept:
			frappe.throw("No Department found. Create a Department (with an abbreviation) first.")
		name = (d.get("item_name") or ci).strip()
		pl_over = {k: d.get("pl_" + k) for k in _PL_KEYS if d.get("pl_" + k) not in (None, "")}
		res = create_fg_item(
			item_name=name, description=name,
			item_group=(d.get("item_group") or ig_default), department=dept,
			stock_uom=(d.get("stock_uom") or "Nos"), cost_item=ci,
			customer_ref=(d.get("customer_ref") or None), pl_overrides=pl_over,
		)
		created.append(res["item_code"])
	frappe.db.commit()
	return {"created": created, "existing": existing}


# ── Create FG on the Savinda Quotation (per cost item) ────────────────────────
def _pl_defaults_from_cost_item(ci):
	"""pl_* defaults derived from a cost Item and its Calculation Breakdown, for the
	quotation FG popup. Fields we can't derive are left blank for the user to fill."""
	ci_doc = frappe.db.get_value(
		"cost Item", ci, ["cost_item_name", "colour", "dimensions"], as_dict=True) or {}
	cb = frappe.db.get_value(
		"Cost Item Calculation", {"parent": ci}, "calculation_breakdown", order_by="idx asc")
	cbd = jt_api._cb_fields(cb) if cb else {}
	pricing = (cbd.get("pricing_type") or "Offset")
	is_flexo = pricing == "Flexo"
	# Product size is taken from the cost Item's dimensions (falls back to the breakdown's
	# carton size) so the FG popup pre-fills it from the costing.
	product_size = (ci_doc.get("dimensions") or "").strip() \
		or (frappe.db.get_value("Calculation Breakdown", cb, "carton_size") if cb else "") or ""
	return {
		"item_name": ci_doc.get("cost_item_name") or ci,
		"pricing": pricing, "is_flexo": is_flexo,
		"department": _dept_for_pricing(pricing),
		"pl_department": pricing,
		"pl_flexo_type": "Reel" if is_flexo else "",
		"pl_product_size": product_size,
		"pl_no_of_colors": cint(cbd.get("no_of_colors") or ci_doc.get("colour") or 0),
		"pl_no_of_ups": cint(cbd.get("no_of_ups") or 0),
		"pl_full_sheet_size": jt_api._size_str(cbd.get("full_sheet_l"), cbd.get("full_sheet_w")),
		"pl_cut_sheet_size": jt_api._size_str(cbd.get("cut_sheet_l"), cbd.get("cut_sheetw")),
		"pl_width_mm": flt(cbd.get("_reel_width")),
		"pl_length_mm": flt(cbd.get("_reel_length")),
	}


@frappe.whitelist()
def get_cost_item_fg_defaults(cost_item, quotation=None):
	"""Proposed FG + Product Library review values for one cost Item (quotation FG popup).
	`quotation` (optional) supplies artwork defaults from its Cost Sheet, when linked."""
	if not cost_item or not frappe.db.exists("cost Item", cost_item):
		return {"defaults": {}, "pl_fields": _pl_field_schema(), "is_flexo": False}
	d = _pl_defaults_from_cost_item(cost_item)
	if quotation:
		cs = frappe.db.get_value("Savinda Quotation", quotation, "cost_sheet")
		if cs:
			d["pl_artwork_no"] = frappe.db.get_value("Cost Sheet", cs, "artwork_no") or ""
			d["pl_artwork_version"] = frappe.db.get_value("Cost Sheet", cs, "artwork_version") or ""
	return {"defaults": d, "pl_fields": _pl_field_schema(), "is_flexo": d.get("is_flexo", False)}


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
	# po_items: native ERPNext pull from the Sales Order so the standard Production Plan →
	# Work Order flow runs; the bespoke builder is only used for the dormant NPD Request source.
	if source_type == "Sales Order":
		_populate_po_items_from_so(pp, source_name)
	else:
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


def _fgs_from_cost_sheet(cs_name):
	"""FG Items behind a Cost Sheet's cost items (via Item.custom_cost_item), each with its
	first Calculation Breakdown. Used by the light NPD flow (FGs are created on the Cost
	Sheet, so the NPD pulls them from there)."""
	out, seen = [], set()
	if not cs_name or not frappe.db.exists("Cost Sheet", cs_name):
		return out
	cs = frappe.get_doc("Cost Sheet", cs_name)
	for row in (cs.get("pricing_list") or []):
		ci = row.get("item")
		if not ci:
			continue
		cb = frappe.db.get_value(
			"Cost Item Calculation", {"parent": ci}, "calculation_breakdown", order_by="idx asc") or ""
		for it in frappe.get_all("Item", filters={"custom_cost_item": ci}, fields=["name", "item_name"]):
			if it.name in seen:
				continue
			seen.add(it.name)
			out.append({
				"fg_item": it.name, "item_name": it.item_name or it.name,
				"cost_item": ci, "calculation_breakdown": cb,
			})
	return out


def _gather_ticket(source_type, source_name):
	"""Return (ticket_type, pricing_type, header_dict, ticket_lines) for a source."""
	if source_type == "NPD Request":
		src = frappe.get_doc("NPD Request", source_name)
		pricing = src.pricing_type or "Offset"
		sample_qty = flt(src.get("sample_qty")) or 1
		lines = []
		# NPD Request is now a light doc — FGs come from its Cost Sheet's cost items
		# (FG Items are created on the Cost Sheet before the NPD).
		for fg in _fgs_from_cost_sheet(src.get("cost_sheet")):
			geom = {"description": fg["item_name"], "cost_item": fg["cost_item"],
				"calculation_breakdown": fg["calculation_breakdown"]}
			bom = _resolve_bom_no(fg["fg_item"]) if fg["fg_item"] else None
			lines.append(_ticket_line(fg["fg_item"], sample_qty, geom, bom))
		header = {
			"custom_npd_request": src.name, "custom_customer": src.customer,
			"custom_customer_name": src.customer_name, "custom_colors": cint(src.colors),
			"custom_job_title": src.job_title, "custom_material": src.material,
			"custom_job_board": src.material, "custom_finishings": src.finishings,
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


def _populate_po_items_from_so(pp, so_name):
	"""Populate po_items natively from a Sales Order (ERPNext ProductionPlan.get_items), so
	the standard Production Plan → Work Order pipeline runs. Sets get_items_from + the
	sales_orders reference, then falls back to the manual builder if the native pull yields
	nothing (e.g. FGs without a default BOM)."""
	so = frappe.get_doc("Sales Order", so_name)
	pp.get_items_from = "Sales Order"
	if not any((r.get("sales_order") == so_name) for r in (pp.get("sales_orders") or [])):
		pp.append("sales_orders", {
			"sales_order": so.name,
			"sales_order_date": so.transaction_date,
			"customer": so.customer,
			"grand_total": flt(so.get("base_grand_total") or so.get("grand_total")),
		})
	try:
		pp.get_items()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "PP native get_items from SO failed")
	if not (pp.get("po_items") or []):
		_build_po_items(pp)


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
	try:
		_stamp_planning_line_fields(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "production_plan stamp planning line fields failed")


def _stamp_planning_line_fields(doc):
	"""Store per-line planning-list fields on each Job Ticket line (draft only):
	No. of Colors, Pass Count = ceil(colours / machine colour capacity), and the Finishings list
	from the FG's Product Library. Computed values also fall back into get_ticket_print_data."""
	if doc.docstatus != 0:
		return
	for tl in (doc.get("custom_ticket_items") or []):
		fg = tl.get("fg_item")
		if not fg:
			continue
		pl_name = jt_api._pl_name_for_fg(fg)
		machine = frappe.db.get_value("Product Library", pl_name, "printing_machine") if pl_name else ""
		colors = 0
		if tl.get("bom_no") and frappe.db.exists("BOM", tl.bom_no):
			colors = cint(frappe.db.get_value("BOM", tl.bom_no, "custom_no_of_colors"))
		if not colors and pl_name:
			colors = cint(frappe.db.get_value("Product Library", pl_name, "no_of_colors"))
		tl.no_of_colors = colors
		tl.pass_count = jt_api.pass_count(colors, machine)
		fins = jt_api.pl_finishings_text(fg)
		if fins:
			tl.finishings = fins


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


# ── Manufacturing planning: "Get Finished Goods for Manufacture" ─────────────
@frappe.whitelist()
def get_manufacture_fg_list(production_plan):
	"""FG list (with default qty + cost links) to show in the Get-Finished-Goods dialog."""
	pp = frappe.get_doc("Production Plan", production_plan)
	out = []
	seen = set()
	for tl in (pp.get("custom_ticket_items") or []):
		key = (tl.get("fg_item") or "", tl.get("calculation_breakdown") or "", tl.get("description") or "")
		if key in seen:
			continue
		seen.add(key)
		out.append({
			"fg_item": tl.get("fg_item") or "",
			"item_name": tl.get("description") or tl.get("fg_item") or "",
			"cost_item": tl.get("cost_item") or "",
			"calculation_breakdown": tl.get("calculation_breakdown") or "",
			"qty": flt(tl.get("qty")) or 1,
		})
	if not out:
		# Native plan without ticket items — fall back to the source lines.
		source_type, source_name = _resolve_source(pp)
		if source_type:
			_ttype, _pr, _hdr, lines = _gather_ticket(source_type, source_name)
			for tl in lines:
				out.append({
					"fg_item": tl.get("fg_item") or "",
					"item_name": tl.get("description") or tl.get("fg_item") or "",
					"cost_item": tl.get("cost_item") or "",
					"calculation_breakdown": tl.get("calculation_breakdown") or "",
					"qty": flt(tl.get("qty")) or 1,
				})
	return out


def _recompute_sheet(cb_name, qty):
	"""Recompute the calculator sheet for a Calculation Breakdown at a given qty.
	Returns (sheet_dict, form_dict)."""
	from nxtgen_savinda_pricing_calculator.api.offset_calculator import calculate
	doc = frappe.get_doc("Calculation Breakdown", cb_name)
	state = json.loads(doc.ui_state or "{}") or {}
	form = dict(state.get("form", {}) or {})
	if qty:
		form["item_qty"] = flt(qty)
	payload = {
		"form": form,
		"machine_spec": state.get("machine_spec"),
		"selected_specs": state.get("selected_specs", []) or [],
	}
	res = calculate(json.dumps(payload)) or {}
	return res.get("sheet", {}) or {}, form


def _plan_print_data(cb_name, qty, pricing_type):
	"""Print figures for one planning row at the given qty."""
	out = {"ups": 0, "cuts": 0, "full_sheet_qty": 0.0, "cut_sheet_qty": 0.0, "wastage": 0.0, "reel_area": 0.0}
	if not cb_name:
		return out
	try:
		sheet, form = _recompute_sheet(cb_name, qty)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "planning recompute failed")
		return out
	out["cuts"] = cint(form.get("no_of_cuts"))
	if (pricing_type or "Offset") == "Flexo":
		out["ups"] = cint(sheet.get("ups"))
		out["reel_area"] = flt(sheet.get("reel_area"))
	else:
		out["ups"] = cint(form.get("no_of_ups"))
		out["full_sheet_qty"] = flt(sheet.get("full_sheet_qty"))
		out["cut_sheet_qty"] = flt(sheet.get("cut_sheet_qty"))
		out["wastage"] = flt(sheet.get("wastage"))
	return out


@frappe.whitelist()
def add_planning_items(production_plan, selections, consolidate=0):
	"""Add selected FGs to the Offset/Flexo planning table with print data computed from
	each item's cost calculation at the (possibly edited) qty.

	consolidate (Offset only): the selection is ONE combined print run — full/cut sheet qty
	and wastage are computed for the whole group (first item's CB at the total qty) and put
	on the FIRST row only; every row still carries its own ups & cuts."""
	if isinstance(selections, str):
		selections = frappe.parse_json(selections) or []
	consolidate = cint(consolidate)

	pp = frappe.get_doc("Production Plan", production_plan)
	pricing = pp.get("custom_pricing_type") or "Offset"
	is_flexo = pricing == "Flexo"
	# NPD samples don't run full sheets — only ups & cuts are needed for the print.
	npd_mode = (pp.get("custom_ticket_type") or "Job") == "NPD"
	field = "custom_flexo_planning" if is_flexo else "custom_offset_planning"

	# Combined-run sheet figures (offset consolidate): first CB at the total selected qty.
	combined = None
	if consolidate and not is_flexo and not npd_mode and selections:
		total_qty = sum(flt(s.get("qty")) for s in selections)
		combined = _plan_print_data(selections[0].get("calculation_breakdown"), total_qty, pricing)

	pp.set(field, [])
	for i, sel in enumerate(selections):
		cb = sel.get("calculation_breakdown")
		qty = flt(sel.get("qty"))
		pd = _plan_print_data(cb, qty, pricing)
		row = {
			"fg_item": sel.get("fg_item") or "",
			"item_name": sel.get("item_name") or sel.get("fg_item") or "",
			"cost_item": sel.get("cost_item") or "",
			"calculation_breakdown": cb or "",
			"qty": qty, "ups": pd["ups"], "cuts": pd["cuts"],
		}
		if is_flexo:
			row["reel_area"] = pd["reel_area"]
		elif npd_mode:
			pass  # NPD sample: ups & cuts only, no sheet qty.
		elif consolidate:
			# Sheet qty + wastage only on the first row (the combined run).
			if i == 0:
				row["full_sheet_qty"] = (combined or pd)["full_sheet_qty"]
				row["cut_sheet_qty"] = (combined or pd)["cut_sheet_qty"]
				row["wastage"] = (combined or pd)["wastage"]
				row["is_first"] = 1
		else:
			row["full_sheet_qty"] = pd["full_sheet_qty"]
			row["cut_sheet_qty"] = pd["cut_sheet_qty"]
			row["wastage"] = pd["wastage"]
		pp.append(field, row)

	pp.save(ignore_permissions=True)
	return {"added": len(selections), "field": field, "pricing_type": pricing}


def _ensure_planning(pp):
	"""If the planning table for the plan's pricing type is empty, auto-generate it from
	custom_ticket_items (print figures via the cost calc) and save. Draft plans only.
	Returns True if it generated rows."""
	if pp.docstatus != 0:
		return False
	is_flexo = (pp.get("custom_pricing_type") or "Offset") == "Flexo"
	field = "custom_flexo_planning" if is_flexo else "custom_offset_planning"
	if pp.get(field):
		return False
	tickets = pp.get("custom_ticket_items") or []
	if not tickets:
		return False
	selections = [{
		"fg_item": t.get("fg_item"), "item_name": t.get("description"),
		"cost_item": t.get("cost_item"), "calculation_breakdown": t.get("calculation_breakdown"),
		"qty": flt(t.get("qty")) or 1,
	} for t in tickets]
	try:
		add_planning_items(pp.name, selections, consolidate=0)
		return True
	except Exception:
		frappe.log_error(frappe.get_traceback(), "auto _ensure_planning failed")
		return False


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
		# Artwork is validated on the Cost Sheet — pull its status + the quotation onto the
		# plan (SO plans) rather than approving artwork here.
		_fetch_artwork_quotation(doc)
		notify_role("BOM Team", "Production Plan created: " + name, _msg(doc, "was created — please validate / create the BOM."), doc)
		notify_role("Supply Chain", "Production Plan created: " + name, _msg(doc, "was created — stock validation follows BOM validation."), doc)
	elif new_state == "BOM Validation":
		# CS Team released the job to planning (Draft -> BOM Validation): stamp the release time
		# once (keep the first release timestamp on any later re-entry).
		if not doc.get("custom_cs_released_on"):
			_stamp(name, {"custom_cs_released_on": now()})
		notify_role("BOM Team", "BOM validation needed: " + name, _msg(doc, "needs BOM validation — create/confirm BOMs (Open BOM Builder / Sync BOMs), then Confirm BOM."), doc)
	elif new_state == "Pre-Print Validation":
		# BOM confirmed by the BOM team → stamp and hand off to the Pre-Print team.
		_stamp(name, {"custom_bom_confirmed": 1, "custom_bom_by": _fullname(), "custom_bom_on": now()})
		notify_role("Pre-Print Team", "Pre-print validation needed: " + name, _msg(doc, "BOM confirmed — please complete pre-print validation, then Validate Pre-Print."), doc)
	elif new_state == "Supply Chain Validation":
		# Pre-print validated → stamp and hand off to Supply Chain for stock validation.
		_stamp(name, {"custom_preprint_by": _fullname(), "custom_preprint_on": now()})
		notify_role("Supply Chain", "Stock validation needed: " + name, _msg(doc, "pre-print validated — please validate stock."), doc)
	elif new_state == "Approved":
		_run_stock_stub(doc)
		_stamp(name, {"custom_stock_validated": 1, "custom_checked_by": _fullname(), "custom_checked_on": now()})
		notify_role("Manufacturing User", "Ready to submit: " + name, _msg(doc, "is approved — ready to submit."), doc)
	elif new_state == "Rejected":
		# The reject popup (production_plan.js before_workflow_action) carries a mandatory
		# reason on custom_reject_remark. Post it to the timeline, fold it into the email,
		# then clear the carrier so a later save can't re-post it.
		remark = (doc.get("custom_reject_remark") or "").strip()
		if remark:
			try:
				doc.add_comment("Comment", "Rejected: " + remark)
			except Exception:
				frappe.log_error(frappe.get_traceback(), "production_plan: reject remark comment failed")
			_stamp(name, {"custom_reject_remark": ""})
		body = _msg(doc, "was rejected.") + ((" Reason: " + remark) if remark else "")
		notify_role("CS Team", "Production Plan rejected: " + name, body, doc)
	elif new_state == "Submitted":
		notify_role("CS Team", "Production Plan submitted: " + name, _msg(doc, "was submitted — production documents can now be created."), doc)


def _fetch_artwork_quotation(doc):
	"""For a plan sourced from a Sales Order, pull the artwork-approval status and quotation
	from the linked Cost Sheet / Savinda Quotation (artwork is validated on the Cost Sheet,
	not on the plan). Best-effort; never blocks."""
	try:
		cost_item = None
		for tl in (doc.get("custom_ticket_items") or []):
			if tl.get("cost_item"):
				cost_item = tl.cost_item
				break
		if not cost_item:
			return
		cs = frappe.db.get_value("Cost Sheet Items", {"item": cost_item}, "parent")
		if not cs:
			return
		vals = {}
		aw = frappe.db.get_value("Cost Sheet", cs, "artwork_status")
		if aw:
			vals["custom_artwork_status"] = aw
		sq = frappe.db.get_value(
			"Savinda Quotation", {"cost_sheet": cs}, "name", order_by="creation desc")
		if sq and not doc.get("custom_quote_no"):
			vals["custom_quote_no"] = sq
		if vals:
			_stamp(doc.name, vals)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "fetch artwork/quotation failed")


# ── NPD sample flow: NPD Request -> Material Request (Manufacture) -> Production Plan ──

def _npd_warehouse(item_code=None):
	"""Target warehouse for a manufactured sample: the item's default warehouse, else any
	non-group warehouse."""
	if item_code:
		wh = frappe.db.get_value("Item Default", {"parent": item_code}, "default_warehouse")
		if wh:
			return wh
	return frappe.db.get_value("Warehouse", {"is_group": 0, "disabled": 0}, "name")


def _npd_sample_fg_items(npd):
	"""FG items to manufacture for an NPD sample (qty = sample_qty), resolved from the linked
	Cost Sheet's cost items (the FG items created for them, incl. variants)."""
	qty = cint(npd.get("sample_qty")) or 1
	cost_items = []
	if npd.get("cost_sheet") and frappe.db.exists("Cost Sheet", npd.cost_sheet):
		cs = frappe.get_doc("Cost Sheet", npd.cost_sheet)
		cost_items = [r.item for r in (cs.get("pricing_list") or []) if r.item]
	out, seen = [], set()
	for ci in cost_items:
		for it in frappe.get_all("Item", filters={"custom_cost_item": ci, "disabled": 0}, pluck="name"):
			if it not in seen:
				seen.add(it)
				out.append({"item_code": it, "qty": qty})
	return out


def _create_npd_material_request(npd):
	"""Create + submit a Manufacture Material Request for the NPD sample FG items; link it back
	on the NPD Request. Idempotent (reuses an existing linked Material Request)."""
	if npd.get("material_request") and frappe.db.exists("Material Request", npd.material_request):
		return npd.material_request
	fgs = _npd_sample_fg_items(npd)
	if not fgs:
		frappe.throw("No Finished-Good items found for this NPD sample. Create the FG items on the "
		             "Cost Sheet / Quotation first.")
	sched = npd.get("required_date") or add_days(today(), 7)
	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Manufacture"
	mr.company = jt_api._default_company()
	mr.transaction_date = today()
	mr.schedule_date = sched
	for fg in fgs:
		mr.append("items", {
			"item_code":     fg["item_code"],
			"qty":           fg["qty"],
			"schedule_date": sched,
			"warehouse":     _npd_warehouse(fg["item_code"]),
			"uom":           frappe.db.get_value("Item", fg["item_code"], "stock_uom") or "Nos",
		})
	mr.insert(ignore_permissions=True)
	mr.submit()
	frappe.db.set_value("NPD Request", npd.name, "material_request", mr.name)
	frappe.db.commit()
	return mr.name


def on_npd_request_update(doc, method=None):
	"""When an NPD Request reaches Approved (workflow), auto-create its Manufacture Material
	Request for the sample FG items."""
	if doc.get("workflow_state") != "Approved":
		return
	before = doc.get_doc_before_save()
	if before and before.get("workflow_state") == "Approved":
		return
	if doc.get("material_request") and frappe.db.exists("Material Request", doc.material_request):
		return
	mr = _create_npd_material_request(doc)
	frappe.msgprint("Material Request <b>{0}</b> (Manufacture) created for the NPD sample. "
	                "Use 'Create Production Plan' next.".format(mr), indicator="green", alert=True)


@frappe.whitelist()
def create_plan_from_npd_mr(npd_request):
	"""Create a DRAFT Production Plan natively from the NPD Request's Material Request
	(get_items_from = Material Request) so the standard Work Order pipeline runs for the sample."""
	npd = frappe.get_doc("NPD Request", npd_request)
	if not npd.get("material_request") or not frappe.db.exists("Material Request", npd.material_request):
		frappe.throw("No Material Request yet — approve the NPD Request first.")
	if npd.get("production_plan") and frappe.db.exists("Production Plan", npd.production_plan):
		return {"production_plan": npd.production_plan, "existing": True}
	mr = frappe.get_doc("Material Request", npd.material_request)
	pp = frappe.new_doc("Production Plan")
	pp.company = jt_api._default_company()
	pp.posting_date = today()
	pp.get_items_from = "Material Request"
	pp.append("material_requests", {"material_request": mr.name, "material_request_date": mr.transaction_date})
	try:
		pp.get_items()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "create_plan_from_npd_mr: get_items failed")
	pp.custom_ticket_type = "NPD"
	pp.custom_pricing_type = "Flexo" if (npd.get("pricing_type") == "Flexo") else "Offset"
	pp.custom_customer = npd.get("customer") or ""
	pp.custom_customer_name = npd.get("customer_name") or ""
	pp.custom_job_title = npd.get("job_title") or npd.name
	if pp.meta.get_field("custom_npd_request"):
		pp.custom_npd_request = npd.name
	pp.custom_created_by = _fullname()
	pp.custom_created_on = now()
	pp.workflow_state = "Draft"
	needs_bom = not (pp.get("po_items") or [])
	pp.custom_needs_bom = 1 if needs_bom else 0
	pp.custom_bom_confirmed = 0 if needs_bom else 1
	pp.insert(ignore_permissions=True)
	frappe.db.set_value("NPD Request", npd.name, "production_plan", pp.name)
	frappe.db.commit()
	return {"production_plan": pp.name, "needs_bom": needs_bom}


@frappe.whitelist()
def create_npd_request_from_quotation(quotation):
	"""Start an NPD sample request from a Savinda Quotation (uses its linked Cost Sheet for the
	FG items). Returns the new NPD Request name."""
	q = frappe.db.get_value("Savinda Quotation", quotation,
	    ["cost_sheet", "inquiry", "customer", "customer_name"], as_dict=True)
	if not q:
		frappe.throw("Quotation not found.")
	doc = frappe.new_doc("NPD Request")
	if q.get("cost_sheet"):
		_npd_from_cost_sheet(doc, q.cost_sheet)
	if q.get("customer"):
		doc.customer = q.customer
	if q.get("customer_name"):
		doc.customer_name = q.customer_name
	doc.quote_no = quotation
	doc.workflow_state = "Draft"
	doc.insert(ignore_permissions=True)
	return {"npd_request": doc.name}
