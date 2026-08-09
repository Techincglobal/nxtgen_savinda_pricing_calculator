"""
Custom Jinja methods registered via hooks.py jinja.methods.
Available in all Frappe Jinja contexts (print formats, email templates, etc.)
"""
import json
import frappe
from frappe.utils import flt, format_date, getdate


def get_cb_finishings(calculation_breakdown):
	"""Finishing labels for a Calculation Breakdown, in selection order. Used on the quotation
	print (with the colour statement) and copied to the Product Library on FG creation.

	Prefers the explicit `finishing` multi-select on the CB (chosen in the calculator — the
	display-only finishings). Falls back, for older CBs that have none, to the selected specs
	whose Offset Spec `group` is 'Finishing'."""
	if not calculation_breakdown or not frappe.db.exists("Calculation Breakdown", calculation_breakdown):
		return []
	# 1. Explicit finishing multi-select (authoritative).
	out = []
	try:
		for row in (frappe.get_doc("Calculation Breakdown", calculation_breakdown).get("finishing") or []):
			name = (row.finishing or "").strip()
			if name and name not in out:
				out.append(name)
	except Exception:
		out = []
	if out:
		return out
	# 2. Fallback: Finishing-group selected specs from ui_state (legacy behaviour).
	try:
		specs = (json.loads(
			frappe.db.get_value("Calculation Breakdown", calculation_breakdown, "ui_state") or "{}"
		) or {}).get("selected_specs") or []
	except Exception:
		specs = []
	for sp in specs:
		name = (sp.get("spec_name") or "").strip()
		if name and name not in out and frappe.db.get_value("Offset Spec", name, "group") == "Finishing":
			out.append(name)
	return out


def get_cb_print_data(doc_name):
	"""
	Prepare all data needed for the 'Product Costing Summary' print format.
	Returns a dict safe to use directly in Jinja templates.
	"""
	doc = frappe.get_doc("Calculation Breakdown", doc_name)

	# ── Parse ui_state ───────────────────────────────────────────────────────
	state = {}
	try:
		state = json.loads(doc.ui_state or "{}")
	except Exception:
		pass

	form		 = state.get("form", {}) or {}
	calc_result  = state.get("calc_result", {}) or {}

	# Recompute the calculation live so the print always reflects the CURRENT
	# calculation logic (e.g. the corrected Extra Production Cost base) instead
	# of the calc_result snapshot stored the last time the CB was saved. Old
	# CBs carry a stale snapshot in ui_state; recomputing keeps the printed PDF
	# consistent with the calculator. Falls back to the stored snapshot on any
	# error so printing never breaks.
	try:
		from nxtgen_savinda_pricing_calculator.api.offset_calculator import calculate as _calculate
		_payload = {
			"form":           form,
			"machine_spec":   state.get("machine_spec"),
			"selected_specs": state.get("selected_specs", []) or [],
		}
		_live = _calculate(json.dumps(_payload))
		if _live and _live.get("cost_rows"):
			calc_result = _live
	except Exception:
		frappe.log_error(frappe.get_traceback(), "get_cb_print_data live recompute failed")

	sheet		 = calc_result.get("sheet", {}) or {}
	pricing		 = calc_result.get("pricing", {}) or {}
	group_totals = calc_result.get("group_totals", {}) or {}
	cost_rows	 = calc_result.get("cost_rows", []) or []

	# ── Group cost rows ───────────────────────────────────────────────────────
	def grp(g):
		return [r for r in cost_rows if (r.get("cost_group") or "").lower() == g]

	mat_rows  = grp("material")
	prep_rows = grp("preparation")
	prod_rows = grp("production")

	# ── Row descriptions ──────────────────────────────────────────────────────
	def row_desc(row):
		cf  = row.get("cost_fact", "")
		sin = row.get("selected_item_name", "")
		sn  = row.get("spec_name", "")
		if sn == "Base Material":
			return sin or cf
		if sin and sin != cf:
			return f"{cf}: {sin}"
		return cf

	for r in cost_rows:
		r["_desc"] = row_desc(r)

	# ── Breakdown name from linked Cost Item Calculation ─────────────────────
	breakdown_name = ""
	try:
		ci_calc = frappe.db.get_value(
			"Cost Item Calculation",
			{"calculation_breakdown": doc_name},
			["description", "parent"],
			as_dict=True,
		)
		if ci_calc:
			ci_name = frappe.db.get_value("cost Item", ci_calc.parent, "cost_item_name")
			breakdown_name = ci_name or ci_calc.description or ci_calc.parent or ""
	except Exception:
		pass

	# ── Foil detection ────────────────────────────────────────────────────────
	has_gold = has_silver = False
	for spec in state.get("selected_specs", []):
		for foil in ((spec.get("machine_assignment") or {}).get("foils") or []):
			fn = (foil.get("foil_name") or "").lower()
			if "gold"   in fn: has_gold   = True
			if "silver" in fn: has_silver = True

	# ── Pricing values ────────────────────────────────────────────────────────
	item_qty	 = float(doc.item_qty or 0)
	grand_total  = float(group_totals.get("grand", 0))
	unit_cost	 = float(pricing.get("unit_cost", 0))
	sscl_total	 = float(pricing.get("sscl", 0))
	vat_total	 = float(pricing.get("vat", 0))
	sell_unit	 = float(pricing.get("sell_unit", 0))
	sell_total	 = float(pricing.get("sell_total", 0))
	mat_contrib  = float(pricing.get("mat_contrib", 0))
	profit_margin = float(doc.profit_margin or 0)

	sscl_per_unit = sscl_total / item_qty if item_qty else 0
	vat_per_unit  = vat_total  / item_qty if item_qty else 0
	final_value   = sell_unit + vat_per_unit

	# Extra production cost is applied on top of the net cost (not a breakdown line).
	net_cost       = float(pricing.get("net_cost", 0)) or grand_total
	extra_prod_amt = float(pricing.get("extra_prod_amt", 0))
	extra_prod_pct = float(pricing.get("extra_prod_pct", 0))

	# ── User display names ────────────────────────────────────────────────────
	def user_name(user_id):
		return frappe.db.get_value("User", user_id, "full_name") or user_id or ""

	# ── Offset-specific sheet data ────────────────────────────────────────────
	cut_sheet_w = float(form.get("cut_sheet_w") or form.get("cut_sheetw") or 0)

	return {
		"doc":			  doc,
		"form":			  form,
		"sheet":		  sheet,
		"pricing":		  pricing,
		"group_totals":   group_totals,
		"mat_rows":		  mat_rows,
		"prep_rows":	  prep_rows,
		"prod_rows":	  prod_rows,
		"breakdown_name": breakdown_name,
		"has_gold":		  has_gold,
		"has_silver":	  has_silver,
		"is_flexo":		  (doc.pricing_type or "Offset") == "Flexo",
		# Pricing
		"item_qty":		  item_qty,
		"grand_total":	  grand_total,
		"unit_cost":	  unit_cost,
		"sscl_per_unit":  sscl_per_unit,
		"vat_per_unit":   vat_per_unit,
		"sell_unit":	  sell_unit,
		"sell_total":	  sell_total,
		"mat_contrib":	  mat_contrib,
		"profit_margin":  profit_margin,
		"final_value":	  final_value,
		"net_cost":		  net_cost,
		"extra_prod_amt": extra_prod_amt,
		"extra_prod_pct": extra_prod_pct,
		# User info
		"created_by":	  user_name(doc.owner),
		"updated_by":	  user_name(doc.modified_by),
		"created_date":   format_date(doc.creation),
		"updated_date":   format_date(doc.modified),
		# Offset helpers
		"full_sheet_l":   float(doc.full_sheet_l or 0),
		"full_sheet_w":   float(doc.full_sheet_w or 0),
		"cut_sheet_l":	  float(doc.cut_sheet_l or 0),
		"cut_sheet_w":	  cut_sheet_w,
	}


def _company_logo():
	return frappe.db.get_single_value("Website Settings", "banner_image") or ""


def _user_name(user_id):
	return frappe.db.get_value("User", user_id, "full_name") or user_id or ""


def _num(v):
	try:
		return float(v or 0)
	except Exception:
		return 0.0


def get_ticket_print_data(production_plan_name):
	"""Data for the Job Ticket print (from a Production Plan). Header falls back to the cost
	sheet / Product Library / Calculation Breakdown; the line-table sheet figures come from
	the planning table (auto-generated if empty)."""
	doc = frappe.get_doc("Production Plan", production_plan_name)
	is_flexo = (doc.get("custom_pricing_type") or "Offset") == "Flexo"

	# Auto-generate the planning table if empty (draft) so sheet qty is available + stored.
	try:
		from nxtgen_savinda_pricing_calculator.api.production_plan import _ensure_planning
		if _ensure_planning(doc):
			doc.reload()
	except Exception:
		pass

	plan_field = "custom_flexo_planning" if is_flexo else "custom_offset_planning"
	plan_by_fg = {}
	for p in (doc.get(plan_field) or []):
		if p.get("fg_item"):
			plan_by_fg.setdefault(p.fg_item, p)

	lines = []
	ticket_rows = doc.get("custom_ticket_items") or []
	if ticket_rows:
		for r in ticket_rows:
			item_name = r.get("description") or (frappe.db.get_value("Item", r.fg_item, "item_name") if r.get("fg_item") else "") or r.get("fg_item") or ""
			pl = plan_by_fg.get(r.get("fg_item"))
			lines.append({
				"item_code": r.get("fg_item") or "", "item_name": item_name,
				"description": r.get("description") or item_name,
				"qty": _num(r.get("qty")),
				"has_bom": 1 if r.get("has_bom") else 0,
				"product_code": r.get("product_code") or "",
				"size": r.get("size") or "",
				# Item variant value (e.g. the per-variant size/spec) — shown in the Size column.
				"variant_value": (frappe.db.get_value("Item", r.get("fg_item"), "custom_variant_value") if r.get("fg_item") else "") or "",
				"batch_no": r.get("batch_no") or "",
				"pack_date": r.get("pack_date"),
				"exp_date": r.get("exp_date"),
				# Planning table overrides the CB geometry for the sheet figures when present.
				"full_sheets": _num(pl.full_sheet_qty if pl else r.get("full_sheets")),
				"cut_sheets": _num(pl.cut_sheet_qty if pl else r.get("cut_sheets")),
				"wastage": _num(pl.wastage if pl else 0),
				"cuts": int(pl.cuts if pl else (r.get("cuts") or 0)),
				"ups": int(pl.ups if pl else (r.get("ups") or 0)),
				"full_sheet_size": r.get("full_sheet_size") or "",
				"cut_sheet_size": r.get("cut_sheet_size") or "",
				"reel_length": _num(r.get("reel_length")),
				"reel_width": _num(r.get("reel_width")),
				"reel_area": _num(pl.reel_area if pl else r.get("reel_area")),
				"slit_width": r.get("slit_width") or "",
			})
	else:
		for r in (doc.get("po_items") or []):
			item_name = frappe.db.get_value("Item", r.item_code, "item_name") or r.item_code
			lines.append({
				"item_code": r.item_code, "item_name": item_name,
				"description": r.get("description") or item_name,
				"qty": _num(r.planned_qty), "has_bom": 1,
				"product_code": r.get("custom_product_code") or "", "size": r.get("custom_size") or "",
				"variant_value": frappe.db.get_value("Item", r.item_code, "custom_variant_value") or "",
				"batch_no": r.get("custom_batch_no") or "",
				"pack_date": r.get("custom_pack_date"), "exp_date": r.get("custom_exp_date"),
				"full_sheets": _num(r.get("custom_full_sheets")), "cut_sheets": _num(r.get("custom_cut_sheets")),
				"wastage": 0,
				"full_sheet_size": r.get("custom_full_sheet_size") or "", "cut_sheet_size": r.get("custom_cut_sheet_size") or "",
				"cuts": r.get("custom_cuts") or 0, "ups": r.get("custom_ups") or 0,
				"reel_length": _num(r.get("custom_reel_length")), "reel_width": _num(r.get("custom_reel_width")),
				"reel_area": _num(r.get("custom_reel_area")), "slit_width": r.get("custom_slit_width") or "",
			})

	# Base material(s) = the board/paper, which is NOT listed among "other materials" (it is
	# shown via the Board/Paper section + the line's Full/Cut Sheets). Collect them from each
	# line's Calculation Breakdown so they can be excluded.
	base_items = set()
	for r in ticket_rows:
		cbn = r.get("calculation_breakdown")
		if cbn:
			bm = frappe.db.get_value("Calculation Breakdown", cbn, "base_material")
			if bm:
				base_items.add(bm)

	# Materials list = full quantity REQUIRED FOR PRODUCTION (gross, per BOM) — not the
	# (net-of-stock) Material Request qty. Aggregate mr_items by item; use required_bom_qty
	# (the gross BOM requirement) and drop the base material.
	agg = {}
	for m in (doc.get("mr_items") or []):
		if m.item_code in base_items:
			continue
		a = agg.get(m.item_code)
		if not a:
			a = {"item_code": m.item_code,
			     "item_name": m.get("item_name") or frappe.db.get_value("Item", m.item_code, "item_name") or m.item_code,
			     "uom": m.get("uom") or m.get("stock_uom") or "",
			     "gross": 0.0, "net": 0.0, "wastage_qty": 0.0}
			agg[m.item_code] = a
		a["net"] += flt(m.get("quantity"))
		a["gross"] = max(a["gross"], flt(m.get("required_bom_qty")))
		a["wastage_qty"] += flt(m.get("custom_wastage_qty"))
	materials = [{
		"item_code": a["item_code"], "item_name": a["item_name"], "uom": a["uom"],
		"quantity": _num(a["gross"] or a["net"]),  # full production requirement
		"wastage_qty": _num(a["wastage_qty"]),
	} for a in agg.values()]

	# Fallback for draft plans (no Material Request yet): explode each FG's BOM for the full
	# production requirement, still excluding the base material.
	if not materials:
		for r in (doc.get("po_items") or []):
			bom_no = r.get("bom_no")
			if not bom_no or not frappe.db.exists("BOM", bom_no):
				continue
			try:
				bom = frappe.get_doc("BOM", bom_no)
			except Exception:
				continue
			bom_qty = flt(bom.quantity) or 1
			scale = (flt(r.get("planned_qty")) / bom_qty) if bom_qty else flt(r.get("planned_qty"))
			for bi in (bom.get("exploded_items") or bom.get("items") or []):
				if bi.item_code in base_items:
					continue
				materials.append({
					"item_code": bi.item_code,
					"item_name": bi.get("item_name") or frappe.db.get_value("Item", bi.item_code, "item_name") or bi.item_code,
					"uom": bi.get("stock_uom") or bi.get("uom") or "",
					"quantity": _num(flt(bi.get("stock_qty") or bi.get("qty")) * scale),
					"wastage_qty": 0,
				})

	# ── Header context — resolve the first item's Product Library / CB, and the SO ──
	so_no = doc.get("custom_sales_order") or ""
	first = ticket_rows[0] if ticket_rows else None
	fg = first.get("fg_item") if first else ""
	cb_name = first.get("calculation_breakdown") if first else ""
	pl = {}
	if fg:
		pl_name = (frappe.db.get_value("Item", fg, "custom_product_library")
		           or frappe.db.get_value("Product Library", {"fg_item": fg}, "name"))
		if pl_name:
			pl = frappe.db.get_value(
				"Product Library", pl_name,
				["flexo_type", "pcs_per_roll", "core_size", "winding_direction", "no_of_ups",
				 "no_of_colors", "product_size", "artwork_no", "artwork_version",
				 "printing_machine"], as_dict=True) or {}
	cb = {}
	if cb_name and frappe.db.exists("Calculation Breakdown", cb_name):
		cb = frappe.db.get_value(
			"Calculation Breakdown", cb_name,
			["carton_size", "no_of_colors", "no_of_ups"], as_dict=True) or {}
	sales_person = ""
	cs_person = ""
	req_date = doc.get("custom_req_date")
	if so_no and frappe.db.exists("Sales Order", so_no):
		so = frappe.get_doc("Sales Order", so_no)
		# Sales / CS person from the SO custom fields (Employee → name), else the sales team.
		if so.get("custom_sales_person"):
			sales_person = frappe.db.get_value("Employee", so.custom_sales_person, "employee_name") or so.custom_sales_person
		elif so.get("sales_team"):
			sales_person = so.sales_team[0].sales_person
		if so.get("custom_cs_person"):
			cs_person = frappe.db.get_value("Employee", so.custom_cs_person, "employee_name") or so.custom_cs_person
		req_date = req_date or so.get("delivery_date")
		# Per-line packing / expiry / batch / product code from the matching SO line item.
		so_item_by_fg = {}
		for si in so.items:
			so_item_by_fg.setdefault(si.item_code, si)
		for l in lines:
			si = so_item_by_fg.get(l["item_code"])
			if not si:
				continue
			l["pack_date"] = l["pack_date"] or si.get("custom_packing_date")
			l["exp_date"] = l["exp_date"] or si.get("custom_expiry_date")
			l["batch_no"] = l["batch_no"] or si.get("custom_batch_no")
			l["product_code"] = l["product_code"] or si.get("custom_product_code")
	quantity = sum(l["qty"] for l in lines)

	header = {
		"customer": doc.get("custom_customer_name") or doc.get("custom_customer") or "",
		"job_title": doc.get("custom_job_title") or (lines[0]["item_name"] if lines else ""),
		"job_board": doc.get("custom_job_board") or doc.get("custom_material") or "",
		"material": doc.get("custom_material") or "",
		"colors": doc.get("custom_colors") or pl.get("no_of_colors") or cb.get("no_of_colors") or 0,
		"art_no": doc.get("custom_art_no") or pl.get("artwork_no") or "",
		"art_version": doc.get("custom_art_version") or pl.get("artwork_version") or "",
		"color_ref": doc.get("custom_color_ref") or "",
		"po_no": doc.get("custom_po_no") or "",
		"req_date": req_date,
		"quote_no": doc.get("custom_quote_no") or "",
		"printing_machine": doc.get("custom_printing_machine") or pl.get("printing_machine") or "",
		"finishings": doc.get("custom_finishings") or "",
		"remarks": doc.get("custom_remarks") or "",
		"npd_request": doc.get("custom_npd_request") or "",
		"job_date": doc.get("posting_date") or format_date(doc.creation),
		"so_no": so_no,
		"sales_person": sales_person,
		"cs_person": cs_person,
		"quantity": quantity,
		"ctn_size": cb.get("carton_size") or pl.get("product_size") or "",
		"reel_or_sheet": pl.get("flexo_type") or ("Reel" if is_flexo else "Sheet"),
		"pcs_per_roll": pl.get("pcs_per_roll") or 0,
		"ups": pl.get("no_of_ups") or cb.get("no_of_ups") or (lines[0]["ups"] if lines else 0),
		"core_size": pl.get("core_size") or "",
		"winding_direction": pl.get("winding_direction") or "",
	}

	return {
		"doc": doc,
		"is_flexo": is_flexo,
		"ticket_type": doc.get("custom_ticket_type") or "Job",
		"logo": _company_logo(),
		"lines": lines,
		"materials": materials,
		"header": header,
		"approvals": [
			{"label": "Created by",              "by": doc.get("custom_created_by") or "", "on": doc.get("custom_created_on")},
			{"label": "Artwork approved by",     "by": doc.get("custom_artwork_by") or "", "on": doc.get("custom_artwork_on")},
			{"label": "Checked by - Supply chain", "by": doc.get("custom_checked_by") or "", "on": doc.get("custom_checked_on")},
			{"label": "Quoted by",               "by": doc.get("custom_quoted_by") or "", "on": doc.get("custom_quoted_on")},
			{"label": "BOM By",                  "by": doc.get("custom_bom_by") or "", "on": doc.get("custom_bom_on")},
		],
		"created_date": format_date(doc.creation),
	}


def get_npd_print_data(npd_name):
	"""Data for the NPD Request print (2-signature request-stage document)."""
	doc = frappe.get_doc("NPD Request", npd_name)
	is_flexo = (doc.get("pricing_type") or "Offset") == "Flexo"

	lines = []
	for r in (doc.get("items") or []):
		lines.append({
			"item_name": r.get("item_name") or "",
			"fg_item": r.get("fg_item") or "",
			"qty": float(r.get("qty") or 0),
			"size": r.get("size") or "",
			"product_code": r.get("product_code") or "",
			"full_sheets": r.get("full_sheets") or 0,
			"cut_sheets": r.get("cut_sheets") or 0,
			"full_sheet_size": r.get("full_sheet_size") or "",
			"cut_sheet_size": r.get("cut_sheet_size") or "",
			"cuts": r.get("cuts") or 0,
			"ups": r.get("ups") or 0,
			"reel_length": r.get("reel_length") or 0,
			"reel_width": r.get("reel_width") or 0,
			"reel_area": r.get("reel_area") or 0,
			"slit_width": r.get("slit_width") or "",
			"repeat_teeth": r.get("repeat_teeth") or "",
			"repeat_ups": r.get("repeat_ups") or 0,
			"repeat_gaps": r.get("repeat_gaps") or "",
			"across_ups": r.get("across_ups") or 0,
			"across_gaps": r.get("across_gaps") or 0,
			"material_width": r.get("material_width") or "",
		})

	materials = []
	for m in (doc.get("bom_materials") or []):
		materials.append({
			"item": m.get("item") or "",
			"item_name": m.get("item_name") or m.get("item") or "",
			"uom": m.get("uom") or "",
			"quantity": float(m.get("quantity") or 0),
		})

	return {
		"doc": doc,
		"is_flexo": is_flexo,
		"logo": _company_logo(),
		"lines": lines,
		"materials": materials,
		"created_by": doc.get("created_by_name") or "",
		"created_on": doc.get("created_on"),
		"approved_by": doc.get("approved_by_name") or "",
		"approved_on": doc.get("approved_on"),
	}


def get_aod_data(delivery_note):
	"""Data for the AOD (Advice of Delivery) print on a Delivery Note.

	Lists the Packing records behind the Delivery Note (each DN line links its Packing via
	`custom_packing`), and pulls the related quotation + artwork approvals from the
	Production Plan behind the Sales Order."""
	from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.packing.packing import (
		_packing_breakdown,
	)
	dn = frappe.get_doc("Delivery Note", delivery_note)

	sales_order = ""
	packings = []
	seen = set()
	for it in (dn.get("items") or []):
		if it.get("against_sales_order") and not sales_order:
			sales_order = it.against_sales_order
		pk = it.get("custom_packing")
		if not pk or pk in seen or not frappe.db.exists("Packing", pk):
			continue
		seen.add(pk)
		p = frappe.db.get_value(
			"Packing", pk,
			["item", "packed_qty", "extra", "dividers", "total", "type", "job__npd_number"],
			as_dict=True,
		) or {}
		details = frappe.get_all(
			"Packing Details", filters={"parent": pk},
			fields=["no_of_boxes", "pcs_per_box", "weight_of_one_box", "total"], order_by="idx",
		)
		packings.append({
			"packing": pk,
			"item": p.get("item"),
			"item_name": frappe.db.get_value("Item", p.get("item"), "item_name") or p.get("item") or "",
			"packed_qty": float(p.get("packed_qty") or 0),
			"extra": float(p.get("extra") or 0),
			"dividers": float(p.get("dividers") or 0),
			"total": float(p.get("total") or 0),
			"type": p.get("type") or "",
			"production_plan": p.get("job__npd_number") or "",
			"breakdown": _packing_breakdown(pk),
			"details": details,
		})

	# Production Plan behind the SO → quotation + artwork approval details.
	pp = {}
	if sales_order:
		pp = frappe.db.get_value(
			"Production Plan", {"custom_sales_order": sales_order},
			["name", "custom_job_title", "custom_customer_name", "custom_po_no", "custom_quote_no",
			 "custom_artwork_status", "custom_artwork_by", "custom_artwork_on",
			 "custom_art_no", "custom_art_version", "custom_colors"],
			as_dict=True, order_by="creation desc",
		) or {}

	quotation = {}
	q_no = (pp or {}).get("custom_quote_no")
	if q_no and frappe.db.exists("Savinda Quotation", q_no):
		quotation = frappe.db.get_value(
			"Savinda Quotation", q_no,
			["name", "customer_name", "date", "valid_till", "profit_margin", "payment_terms"],
			as_dict=True,
		) or {}

	# Shipping address lines for the header block.
	address_lines = []
	addr_name = dn.get("shipping_address_name") or dn.get("customer_address")
	if addr_name and frappe.db.exists("Address", addr_name):
		a = frappe.get_doc("Address", addr_name)
		for f in ("address_line1", "address_line2", "city", "state", "pincode", "country"):
			v = a.get(f)
			if v:
				address_lines.append(v)

	total_qty = sum(p["packed_qty"] for p in packings)

	return {
		"doc": dn,
		"logo": _company_logo(),
		"sales_order": sales_order,
		"packings": packings,
		"pp": pp or {},
		"quotation": quotation,
		# ── Header fields for the AOD layout ──
		"aod_no": dn.name,
		"date": format_date(dn.get("posting_date")),
		"po_no": (pp or {}).get("custom_po_no") or dn.get("po_no") or dn.get("customer_po_details") or "",
		"job_no": (pp or {}).get("name") or "",
		"customer": dn.get("customer_name") or dn.get("customer") or "",
		"address_lines": address_lines,
		"total_qty": total_qty,
	}
