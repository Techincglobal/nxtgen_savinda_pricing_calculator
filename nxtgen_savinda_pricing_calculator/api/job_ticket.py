"""
Job Ticket / NPD Job Ticket API — build tickets from Sales Order / Inquiry / Cost Sheet,
pull data when a source is selected on the ticket, and hand off to an ERPNext Production Plan.

First stage: the ticket carries a materials list WITH wastage for reference; the real
raw-material requirement is generated later inside the Production Plan (Get Raw Materials
+ the app's existing "Add Wastage" button). No material explosion happens here.
"""
import json
import math

import frappe
from frappe.utils import cint, flt

from nxtgen_savinda_pricing_calculator.api.offset_calculator import _get_config


# ── context / lookup helpers ─────────────────────────────────────────────────
def _default_company():
	return (
		frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.db.get_value("Company", {}, "name")
	)


def _fg_context(fg_item_code):
	"""Resolve FG Item → cost Item → Calculation Breakdown → Product Library."""
	ctx = {"fg_item": fg_item_code or "", "cost_item": "", "calculation_breakdown": "", "product_library": ""}
	if not fg_item_code or not frappe.db.exists("Item", fg_item_code):
		return ctx
	ctx["cost_item"] = frappe.db.get_value("Item", fg_item_code, "custom_cost_item") or ""
	ctx["product_library"] = (
		frappe.db.get_value("Item", fg_item_code, "custom_product_library")
		or frappe.db.get_value("Product Library", {"fg_item": fg_item_code}, "name")
		or ""
	)
	if ctx["cost_item"]:
		ctx["calculation_breakdown"] = frappe.db.get_value(
			"Cost Item Calculation", {"parent": ctx["cost_item"]},
			"calculation_breakdown", order_by="idx asc",
		) or ""
	return ctx


def _cost_item_context(ci_name):
	"""Resolve cost Item → FG Item / Calculation Breakdown / Product Library."""
	ctx = {"fg_item": "", "cost_item": ci_name or "", "calculation_breakdown": "", "product_library": ""}
	if not ci_name:
		return ctx
	ctx["fg_item"] = frappe.db.get_value("Item", {"custom_cost_item": ci_name}, "name") or ""
	ctx["calculation_breakdown"] = frappe.db.get_value(
		"Cost Item Calculation", {"parent": ci_name}, "calculation_breakdown", order_by="idx asc",
	) or ""
	ctx["product_library"] = (
		frappe.db.get_value("Product Library", {"cost_item": ci_name}, "name")
		or (frappe.db.get_value("Item", ctx["fg_item"], "custom_product_library") if ctx["fg_item"] else "")
		or ""
	)
	return ctx


def _cb_fields(cb_name):
	"""Calculation Breakdown fields + flexo reel dims parsed from ui_state."""
	if not cb_name or not frappe.db.exists("Calculation Breakdown", cb_name):
		return {}
	d = frappe.db.get_value(
		"Calculation Breakdown", cb_name,
		["pricing_type", "no_of_colors", "no_of_ups", "no_of_cuts",
		 "full_sheet_l", "full_sheet_w", "cut_sheet_l", "cut_sheetw",
		 "full_sheet_qty", "cut_sheet_qty", "req_cut_sheets",
		 "base_material", "custom_material_name", "item_qty", "carton_size", "ui_state"],
		as_dict=True,
	) or {}
	reel_w = reel_l = reel_area = 0
	if d.get("ui_state"):
		try:
			state = json.loads(d["ui_state"]) or {}
			form = state.get("form", {}) or {}
			calc = (state.get("calc_result") or state.get("calc") or {}) or {}
			sheet = calc.get("sheet") or {}
			reel_w = flt(form.get("reel_width_mm") or form.get("product_width_mm") or 0)
			reel_l = flt(sheet.get("reel_length") or form.get("reel_length_m") or 0)
			reel_area = flt(sheet.get("reel_area") or 0)
		except Exception:
			pass
	d["_reel_width"] = reel_w
	d["_reel_length"] = reel_l
	d["_reel_area"] = reel_area
	return d


def _size_str(a, b):
	a, b = flt(a), flt(b)
	return f"{a} x {b}" if (a or b) else ""


def _wastage_pct(pricing_type, colors):
	"""Job-level wastage fraction from Costing Configuration."""
	cfg = _get_config()
	if (pricing_type or "").strip() == "Flexo":
		colors = cint(colors)
		for max_colors, _setup, pct in (cfg.get("flexo_wastage") or []):
			if colors <= cint(max_colors):
				return flt(pct)
		return 0.0
	return flt(cfg.get("offset_wastage_pct") or 0)


def _material_rate_qty(cb, pricing_type):
	if (pricing_type or "").strip() == "Flexo":
		return flt(cb.get("_reel_area") or 0)
	return flt(cb.get("full_sheet_qty") or cb.get("cut_sheet_qty") or 0)


def _pl(name):
	if not name or not frappe.db.exists("Product Library", name):
		return {}
	return frappe.get_doc("Product Library", name).as_dict()


def _finishings_text(pl):
	if not pl:
		return ""
	rows = frappe.get_all(
		"Product Library Finishing", filters={"parent": pl.get("name")},
		fields=["process_name"], order_by="idx asc",
	)
	return ", ".join([r.process_name for r in rows if r.process_name])


def _pl_name_for_fg(fg_item):
	"""Resolve the Product Library record for an FG Item (via Item link, then fg_item back-ref)."""
	if not fg_item:
		return None
	return (frappe.db.get_value("Item", fg_item, "custom_product_library")
	        or frappe.db.get_value("Product Library", {"fg_item": fg_item}, "name"))


def pl_finishings_text(fg_item):
	"""Finishings stored on the FG's Product Library, comma-joined (planning-list column)."""
	pl_name = _pl_name_for_fg(fg_item)
	if not pl_name:
		return ""
	rows = frappe.get_all(
		"Product Library Finishing", filters={"parent": pl_name},
		fields=["process_name"], order_by="idx asc",
	)
	return ", ".join([r.process_name for r in rows if r.process_name])


def machine_color_capacity(machine_name):
	"""Colour capacity of a printing machine (from the Offset Machine master), matched by name.
	Returns 0 when the machine is unknown or has no capacity set."""
	if not machine_name:
		return 0
	cap = frappe.db.get_value("Offset Machine", machine_name, "color_capacity")
	if cap is None:
		row = frappe.db.sql(
			"select color_capacity from `tabOffset Machine` where lower(machine_name)=lower(%s) limit 1",
			(machine_name,))
		cap = row[0][0] if row else 0
	return cint(cap)


def pass_count(no_of_colors, machine_name):
	"""Print passes = ceil(colours / machine colour capacity). 0 when colours/capacity unknown."""
	colors = cint(no_of_colors)
	cap = machine_color_capacity(machine_name)
	if not colors or not cap:
		return 0
	return int(math.ceil(colors / float(cap)))


# ── row / header builders ────────────────────────────────────────────────────
def _build_ticket_materials(cb_name, pricing_type, colors, merge_into=None):
	"""Job Ticket Material rows for a Calculation Breakdown: board line + non-board
	Material cost-facts, each with a wastage figure. NO explosion."""
	rows = merge_into if merge_into is not None else []
	if not cb_name:
		return rows
	cb = _cb_fields(cb_name)
	if not cb:
		return rows
	pct = _wastage_pct(pricing_type, colors)

	def _add(item, item_name, uom, qty, is_board=0):
		qty = flt(qty)
		if not (item or item_name) or qty <= 0:
			return
		key = (item or item_name, uom)
		for r in rows:
			if (r.get("item") or r.get("item_name"), r.get("uom")) == key:
				r["quantity"] = flt(r["quantity"]) + qty
				r["wastage_qty"] = round(flt(r["quantity"]) * pct, 4)
				return
		rows.append({
			"item": item or "",
			"item_name": item_name or item or "",
			"uom": uom or "",
			"quantity": round(qty, 4),
			"wastage_pct": round(pct * 100, 4),
			"wastage_qty": round(qty * pct, 4),
			"is_board": is_board,
		})

	if cb.get("base_material"):
		board_name = frappe.db.get_value("Item", cb["base_material"], "item_name") or cb["base_material"]
	else:
		board_name = cb.get("custom_material_name") or ""
	_add(cb.get("base_material") or "", board_name,
	     "M2" if pricing_type == "Flexo" else "Nos",
	     _material_rate_qty(cb, pricing_type), is_board=1)

	for cf in frappe.get_all(
		"Cost Fact Details", filters={"parent": cb_name, "cost_group": "Material"},
		fields=["selected_item", "uom", "req_qty"], order_by="idx asc",
	):
		item = cf.get("selected_item") or ""
		iname = (frappe.db.get_value("Item", item, "item_name") if item else "") or item
		_add(item, iname, cf.get("uom") or "", cf.get("req_qty"), is_board=0)

	return rows


def _item_line_from_cb(description, qty, ctx, cb, pl, pricing_type):
	line = {
		"description": description or "",
		"qty": flt(qty),
		"fg_item": ctx.get("fg_item") or "",
		"cost_item": ctx.get("cost_item") or "",
		"calculation_breakdown": ctx.get("calculation_breakdown") or "",
		"product_code": (pl.get("customer_product_code") or "") if pl else "",
		"size": (pl.get("product_size") if pl else "") or cb.get("carton_size") or "",
	}
	if (pricing_type or "").strip() == "Flexo":
		line.update({
			"reel_length": flt(cb.get("_reel_length") or 0),
			"reel_width": flt(cb.get("_reel_width") or 0),
			"reel_area": flt(cb.get("_reel_area") or 0),
		})
	else:
		line.update({
			"full_sheets": flt(cb.get("full_sheet_qty") or 0),
			"cut_sheets": flt(cb.get("cut_sheet_qty") or cb.get("req_cut_sheets") or 0),
			"full_sheet_size": (pl.get("full_sheet_size") if pl else "") or _size_str(cb.get("full_sheet_l"), cb.get("full_sheet_w")),
			"cut_sheet_size": (pl.get("cut_sheet_size") if pl else "") or _size_str(cb.get("cut_sheet_l"), cb.get("cut_sheetw")),
			"cuts": cint(cb.get("no_of_cuts")),
			"ups": cint(cb.get("no_of_ups")),
		})
	return line


def _apply_header_from_context(doc, ctx, cb, pl):
	pricing = (pl.get("department") if pl else "") or cb.get("pricing_type") or "Offset"
	doc.pricing_type = "Flexo" if pricing == "Flexo" else "Offset"
	doc.product_library = ctx.get("product_library") or ""
	doc.colors = cint(cb.get("no_of_colors") or (pl.get("no_of_colors") if pl else 0))
	doc.ups = cint(cb.get("no_of_ups") or (pl.get("no_of_ups") if pl else 0))
	if pl:
		doc.art_no = pl.get("artwork_no") or ""
		doc.art_version = pl.get("artwork_version") or ""
		doc.core_size = pl.get("core_size") or ""
		doc.pcs_per_roll = cint(pl.get("pcs_per_roll") or 0)
		doc.winding_direction = pl.get("winding_direction") or None
		doc.reel_or_sheet = pl.get("flexo_type") or ("Reel" if doc.pricing_type == "Flexo" else "Sheet")
		doc.quote_no = pl.get("quotation_no") or doc.quote_no
		if pl.get("inquiry") and not doc.inquiry:
			doc.inquiry = pl.get("inquiry")
		doc.finishings = _finishings_text(pl)
	if cb.get("base_material"):
		doc.job_board = frappe.db.get_value("Item", cb["base_material"], "item_name") or cb["base_material"]
	else:
		doc.job_board = cb.get("custom_material_name") or doc.job_board
	doc.material = doc.job_board


# ── populate a ticket from a source (shared by creators + pull) ───────────────
def _clear_children(doc):
	doc.set("items", [])
	doc.set("bom_materials", [])


def _populate_from_sales_order(doc, so_name):
	so = frappe.get_doc("Sales Order", so_name)
	doc.sales_order = so.name
	doc.customer = so.customer
	doc.customer_name = so.customer_name
	doc.po_no = so.po_no or ""
	doc.req_date = so.delivery_date
	if not doc.job_date:
		doc.job_date = so.transaction_date
	_clear_children(doc)
	materials = []
	first = None
	for it in so.items:
		ctx = _fg_context(it.item_code)
		cb = _cb_fields(ctx.get("calculation_breakdown"))
		pl = _pl(ctx.get("product_library"))
		pricing = (pl.get("department") if pl else "") or cb.get("pricing_type") or "Offset"
		doc.append("items", _item_line_from_cb(it.item_name or it.item_code, it.qty, ctx, cb, pl, pricing))
		_build_ticket_materials(ctx.get("calculation_breakdown"), pricing, cb.get("no_of_colors"), merge_into=materials)
		if first is None and cb:
			first = (ctx, cb, pl)
	if first:
		_apply_header_from_context(doc, *first)
		doc.job_title = (first[2].get("product_name") if first[2] else "") or doc.customer_name or ""
	for m in materials:
		doc.append("bom_materials", m)


def _populate_from_cost_sheet(doc, cs_name):
	cs = frappe.get_doc("Cost Sheet", cs_name)
	doc.cost_sheet = cs.name
	if cs.get("inquiry"):
		doc.inquiry = cs.get("inquiry")
	doc.customer_name = cs.get("customer_name") or doc.customer_name
	doc.job_title = cs.get("subject") or doc.job_title
	if cs.get("colour"):
		doc.colors = cint(cs.get("colour"))
	_clear_children(doc)
	materials = []
	first = None
	for r in (cs.get("pricing_list") or []):
		if not r.get("item"):
			continue
		ctx = _cost_item_context(r.get("item"))
		cb = _cb_fields(ctx.get("calculation_breakdown"))
		pl = _pl(ctx.get("product_library"))
		pricing = (pl.get("department") if pl else "") or cb.get("pricing_type") or "Offset"
		ci = frappe.db.get_value("cost Item", r.get("item"), ["cost_item_name", "item_qty"], as_dict=True) or {}
		desc = ci.get("cost_item_name") or r.get("item_name") or r.get("item")
		doc.append("items", _item_line_from_cb(desc, r.get("qty") or ci.get("item_qty") or 0, ctx, cb, pl, pricing))
		_build_ticket_materials(ctx.get("calculation_breakdown"), pricing, cb.get("no_of_colors"), merge_into=materials)
		if first is None and cb:
			first = (ctx, cb, pl)
	if first:
		_apply_header_from_context(doc, *first)
	for m in materials:
		doc.append("bom_materials", m)


def _populate_from_inquiry(doc, opp_name):
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
	# The inquiry's Cost Sheet is the richest source; else fall back to a cost item.
	cs = frappe.db.get_value("Cost Sheet", {"inquiry": opp_name}, "name", order_by="creation desc")
	if cs:
		_populate_from_cost_sheet(doc, cs)
		doc.inquiry = opp_name
		return
	_clear_children(doc)
	ci_name = frappe.db.get_value("cost Item", {"inquiry": opp_name}, "name")
	if ci_name:
		ctx = _cost_item_context(ci_name)
		cb = _cb_fields(ctx.get("calculation_breakdown"))
		pl = _pl(ctx.get("product_library"))
		pricing = (pl.get("department") if pl else "") or cb.get("pricing_type") or "Offset"
		ci = frappe.db.get_value("cost Item", ci_name, ["cost_item_name", "item_qty"], as_dict=True) or {}
		if cb:
			_apply_header_from_context(doc, ctx, cb, pl)
			doc.append("items", _item_line_from_cb(ci.get("cost_item_name") or "", ci.get("item_qty") or 0, ctx, cb, pl, pricing))
			for m in _build_ticket_materials(ctx.get("calculation_breakdown"), pricing, cb.get("no_of_colors")):
				doc.append("bom_materials", m)


# ── whitelisted creators ─────────────────────────────────────────────────────
def _new_ticket(ticket_type):
	doc = frappe.new_doc("Job Ticket")
	doc.ticket_type = ticket_type
	return doc


@frappe.whitelist()
def create_job_ticket_from_sales_order(sales_order):
	doc = _new_ticket("Job")
	_populate_from_sales_order(doc, sales_order)
	doc.insert(ignore_permissions=True)
	return {"job_ticket": doc.name}


@frappe.whitelist()
def create_npd_from_sales_order(sales_order):
	doc = _new_ticket("NPD")
	_populate_from_sales_order(doc, sales_order)
	doc.insert(ignore_permissions=True)
	return {"job_ticket": doc.name}


@frappe.whitelist()
def create_job_ticket_from_inquiry(opportunity):
	doc = _new_ticket("Job")
	_populate_from_inquiry(doc, opportunity)
	doc.insert(ignore_permissions=True)
	return {"job_ticket": doc.name}


@frappe.whitelist()
def create_npd_from_inquiry(opportunity):
	doc = _new_ticket("NPD")
	_populate_from_inquiry(doc, opportunity)
	doc.insert(ignore_permissions=True)
	return {"job_ticket": doc.name}


@frappe.whitelist()
def create_npd_from_cost_sheet(cost_sheet):
	doc = _new_ticket("NPD")
	_populate_from_cost_sheet(doc, cost_sheet)
	doc.insert(ignore_permissions=True)
	return {"job_ticket": doc.name}


def _unlinked_materials(source_type, source_name):
	"""Materials that don't resolve to a real ERPNext Item (e.g. inks not yet created)."""
	doc = frappe.new_doc("Job Ticket")
	_populate_by_source(doc, source_type, source_name)
	out = []
	for m in doc.bom_materials:
		if not m.item or not frappe.db.exists("Item", m.item):
			out.append(m.item_name or m.item or "(unnamed)")
	return out


def _apply_material_map(doc, material_map):
	"""Fill the real Item on unlinked material rows from a {sample_name: item_code} map."""
	if not material_map:
		return
	if isinstance(material_map, str):
		try:
			material_map = json.loads(material_map)
		except Exception:
			return
	if not isinstance(material_map, dict) or not material_map:
		return
	for m in doc.bom_materials:
		if m.item and frappe.db.exists("Item", m.item):
			continue
		key = m.item_name or m.item
		chosen = material_map.get(key)
		if chosen and frappe.db.exists("Item", chosen):
			m.item = chosen
			m.item_name = frappe.db.get_value("Item", chosen, "item_name") or m.item_name


@frappe.whitelist()
def create_npd_checked(source_type, source_name, force=0, material_map=None):
	"""Validate materials, then create the NPD. Returns needs_confirm + the unlinked
	material list when some materials have no Item (unless force=1). A {sample_name:
	item_code} material_map links chosen Items onto the created ticket's materials."""
	if not cint(force):
		unlinked = _unlinked_materials(source_type, source_name)
		if unlinked:
			return {"needs_confirm": True, "unlinked": unlinked}
	doc = _new_ticket("NPD")
	_populate_by_source(doc, source_type, source_name)
	_apply_material_map(doc, material_map)
	doc.insert(ignore_permissions=True)
	still = [m.item_name or m.item or "(unnamed)" for m in doc.bom_materials
	         if not m.item or not frappe.db.exists("Item", m.item)]
	return {"job_ticket": doc.name, "unlinked": still}


# ── pull data when a source is selected on the ticket ─────────────────────────
_HEADER_FIELDS = [
	"pricing_type", "customer", "customer_name", "sales_person", "po_no", "req_date",
	"quote_no", "art_no", "art_version", "color_ref", "job_title", "job_board", "material",
	"colors", "quantity", "reel_or_sheet", "pcs_per_roll", "ups", "core_size", "printed",
	"winding_direction", "finishings", "product_library", "cost_sheet", "inquiry", "sales_order",
]
_ITEM_FIELDS = [
	"description", "size", "qty", "fg_item", "cost_item", "calculation_breakdown",
	"product_code", "batch_no", "pack_date", "exp_date", "full_sheets", "cut_sheets",
	"full_sheet_size", "cut_sheet_size", "cuts", "ups", "reel_length", "reel_width",
	"reel_area", "slit_width", "repeat_teeth", "repeat_ups", "repeat_gaps", "across_ups",
	"across_gaps", "material_width", "ups_per_reel", "labels_per_reel",
]
_MAT_FIELDS = ["item", "item_name", "uom", "quantity", "wastage_pct", "wastage_qty", "is_board"]


def _populate_by_source(doc, source_type, source_name):
	if source_type == "Sales Order":
		_populate_from_sales_order(doc, source_name)
	elif source_type == "Cost Sheet":
		_populate_from_cost_sheet(doc, source_name)
	elif source_type == "Opportunity":
		_populate_from_inquiry(doc, source_name)


@frappe.whitelist()
def get_source_data(source_type, source_name):
	"""Return header + child rows populated from a source, for applying to a (possibly new)
	Job Ticket form client-side."""
	doc = frappe.new_doc("Job Ticket")
	_populate_by_source(doc, source_type, source_name)
	return {
		"header": {f: doc.get(f) for f in _HEADER_FIELDS if doc.get(f) not in (None, "")},
		"items": [{k: r.get(k) for k in _ITEM_FIELDS} for r in doc.items],
		"bom_materials": [{k: r.get(k) for k in _MAT_FIELDS} for r in doc.bom_materials],
	}


@frappe.whitelist()
def pull_source_data(job_ticket):
	"""Re-populate a saved draft ticket from whichever source link is set."""
	doc = frappe.get_doc("Job Ticket", job_ticket)
	if doc.docstatus != 0:
		frappe.throw("Data can only be pulled on a draft Job Ticket.")
	if doc.sales_order:
		_populate_from_sales_order(doc, doc.sales_order)
	elif doc.cost_sheet:
		_populate_from_cost_sheet(doc, doc.cost_sheet)
	elif doc.inquiry:
		_populate_from_inquiry(doc, doc.inquiry)
	else:
		return {"ok": False, "msg": "Select a Sales Order, Cost Sheet or Inquiry first."}
	doc.save(ignore_permissions=True)
	return {"ok": True}


# ── Sample FG + BOM (NPD → delivery/invoice at zero cost) ─────────────────────
def _resolve_fg_item_group(jt):
	ig = ""
	if jt.inquiry:
		ig = frappe.db.get_value("Opportunity", jt.inquiry, "custom_item_group")
	if not ig and jt.cost_sheet:
		ig = frappe.db.get_value("Cost Sheet", jt.cost_sheet, "item_group")
	if not ig:
		ig = (
			frappe.db.get_value("Item Group", {"item_group_name": "Finished Goods"}, "name")
			or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
		)
	return ig


def _resolve_department(jt):
	return (
		frappe.db.get_value("Department", {"department_name": jt.pricing_type}, "name")
		or frappe.db.get_value("Department", {"is_group": 0}, "name")
		or frappe.db.get_value("Department", {}, "name")
	)


@frappe.whitelist()
def create_sample_fg_and_bom(job_ticket):
	"""Create a zero-cost sample FG Item + a flat BOM from the ticket's linked materials,
	so an NPD sample can be delivered / invoiced. Idempotent per FG."""
	from nxtgen_savinda_pricing_calculator.api.bom_builder import create_bom
	from nxtgen_savinda_pricing_calculator.api.manufacturing import create_fg_item

	jt = frappe.get_doc("Job Ticket", job_ticket)
	ig = _resolve_fg_item_group(jt)
	dept = _resolve_department(jt)

	# Raw materials = linked materials only (unlinked inks are skipped — informational).
	raw = []
	unlinked = []
	for m in jt.bom_materials:
		if m.item and frappe.db.exists("Item", m.item):
			raw.append({
				"item_code": m.item,
				"item_name": m.item_name or m.item,
				"qty": flt(m.quantity) or 1,
				"uom": m.uom or None,
				"rate": 0,
			})
		else:
			unlinked.append(m.item_name or m.item or "(unnamed)")

	created_fg, created_bom, skipped_bom = [], [], []
	for row in jt.items:
		fg = row.fg_item
		if not fg:
			if not dept:
				frappe.throw("No Department found. Create a Department (with an abbreviation) before creating a sample FG.")
			name = (row.description or jt.job_title or jt.name).strip()
			res = create_fg_item(
				item_name=name, description=name, item_group=ig, department=dept,
				stock_uom="Nos", cost_item=(row.cost_item or None),
			)
			fg = res["item_code"]
			# Write directly on the child row so it works whether or not the ticket is submitted.
			frappe.db.set_value("Job Ticket Item", row.name, "fg_item", fg)
			created_fg.append(fg)

		# BOM — skip if one already active; skip if no linked materials.
		if frappe.db.get_value("BOM", {"item": fg, "docstatus": 1, "is_active": 1}, "name"):
			continue
		if not raw:
			skipped_bom.append(fg)
			continue
		try:
			res = create_bom(fg, flt(row.qty) or 1, [], raw, is_default=1)
			created_bom.append(res.get("bom_name"))
		except Exception as e:
			skipped_bom.append("%s (%s)" % (fg, str(e)[:80]))

	frappe.db.commit()
	return {
		"fg": created_fg,
		"bom": created_bom,
		"skipped_bom": skipped_bom,
		"unlinked": unlinked,
	}


# ── Production Plan hand-off ──────────────────────────────────────────────────
def _default_fg_warehouse(company):
	"""FG warehouse for the Production Plan rows (optional). ERPNext Company has no
	FG-warehouse column in this version — use Manufacturing Settings, else any company
	warehouse, else None (the user sets it in the Production Plan)."""
	wh = frappe.db.get_single_value("Manufacturing Settings", "default_fg_warehouse")
	if not wh and company:
		wh = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	return wh


def _default_bom(fg_item):
	return (
		frappe.db.get_value("Item", fg_item, "default_bom")
		or frappe.db.get_value("BOM", {"item": fg_item, "is_active": 1, "is_default": 1}, "name")
		or frappe.db.get_value("BOM", {"item": fg_item, "is_active": 1}, "name")
	)


@frappe.whitelist()
def create_production_plan(job_ticket):
	jt = frappe.get_doc("Job Ticket", job_ticket)
	company = _default_company()
	fg_wh = _default_fg_warehouse(company)

	pp = frappe.new_doc("Production Plan")
	pp.company = company
	pp.posting_date = frappe.utils.today()

	skipped = []
	added = 0
	for row in jt.items:
		if not row.fg_item:
			continue
		bom = _default_bom(row.fg_item)
		if not bom:
			skipped.append(row.fg_item)
			continue
		qty = flt(row.qty) or 1
		pp.append("po_items", {
			"item_code": row.fg_item,
			"bom_no": bom,
			"planned_qty": qty,
			"pending_qty": qty,
			"warehouse": fg_wh,
		})
		added += 1

	if not added:
		msg = "No FG lines have an active BOM. Build BOMs first via the BOM Builder."
		if skipped:
			msg += " Missing BOM for: " + ", ".join(skipped)
		return {"warning": msg}

	pp.insert(ignore_permissions=True)
	frappe.db.set_value("Job Ticket", jt.name, "production_plan", pp.name)

	warning = ""
	if skipped:
		warning = "These FGs have no BOM and were skipped (build via BOM Builder): " + ", ".join(skipped)
	return {"production_plan": pp.name, "warning": warning}
