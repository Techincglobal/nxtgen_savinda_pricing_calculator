"""
Custom Jinja methods registered via hooks.py jinja.methods.
Available in all Frappe Jinja contexts (print formats, email templates, etc.)
"""
import json
import frappe
from frappe.utils import format_date, getdate


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


def get_ticket_print_data(production_plan_name):
	"""Data for the Job Ticket print (from a Production Plan)."""
	doc = frappe.get_doc("Production Plan", production_plan_name)
	is_flexo = (doc.get("custom_pricing_type") or "Offset") == "Flexo"

	lines = []
	# Prefer the full ticket item list (includes items still awaiting a BOM); fall back to
	# po_items for older plans created before custom_ticket_items existed.
	ticket_rows = doc.get("custom_ticket_items") or []
	if ticket_rows:
		for r in ticket_rows:
			item_name = r.get("description") or (frappe.db.get_value("Item", r.fg_item, "item_name") if r.get("fg_item") else "") or r.get("fg_item") or ""
			lines.append({
				"item_code": r.get("fg_item") or "", "item_name": item_name,
				"description": r.get("description") or item_name,
				"qty": float(r.get("qty") or 0),
				"has_bom": 1 if r.get("has_bom") else 0,
				"product_code": r.get("product_code") or "",
				"size": r.get("size") or "",
				"batch_no": r.get("batch_no") or "",
				"pack_date": r.get("pack_date"),
				"exp_date": r.get("exp_date"),
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
			})
	else:
		for r in (doc.get("po_items") or []):
			item_name = frappe.db.get_value("Item", r.item_code, "item_name") or r.item_code
			lines.append({
				"item_code": r.item_code, "item_name": item_name,
				"description": r.get("description") or item_name,
				"qty": float(r.planned_qty or 0),
				"has_bom": 1,
				"product_code": r.get("custom_product_code") or "",
				"size": r.get("custom_size") or "",
				"batch_no": r.get("custom_batch_no") or "",
				"pack_date": r.get("custom_pack_date"),
				"exp_date": r.get("custom_exp_date"),
				"full_sheets": r.get("custom_full_sheets") or 0,
				"cut_sheets": r.get("custom_cut_sheets") or 0,
				"full_sheet_size": r.get("custom_full_sheet_size") or "",
				"cut_sheet_size": r.get("custom_cut_sheet_size") or "",
				"cuts": r.get("custom_cuts") or 0,
				"ups": r.get("custom_ups") or 0,
				"reel_length": r.get("custom_reel_length") or 0,
				"reel_width": r.get("custom_reel_width") or 0,
				"reel_area": r.get("custom_reel_area") or 0,
				"slit_width": r.get("custom_slit_width") or "",
			})

	materials = []
	for m in (doc.get("mr_items") or []):
		materials.append({
			"item_code": m.item_code,
			"item_name": m.get("item_name") or frappe.db.get_value("Item", m.item_code, "item_name") or m.item_code,
			"uom": m.get("uom") or m.get("stock_uom") or "",
			"quantity": float(m.get("quantity") or 0),
			"wastage_qty": float(m.get("custom_wastage_qty") or 0),
		})

	return {
		"doc": doc,
		"is_flexo": is_flexo,
		"ticket_type": doc.get("custom_ticket_type") or "Job",
		"logo": _company_logo(),
		"lines": lines,
		"materials": materials,
		"header": {
			"customer": doc.get("custom_customer_name") or doc.get("custom_customer") or "",
			"job_title": doc.get("custom_job_title") or "",
			"job_board": doc.get("custom_job_board") or "",
			"material": doc.get("custom_material") or "",
			"colors": doc.get("custom_colors") or 0,
			"art_no": doc.get("custom_art_no") or "",
			"art_version": doc.get("custom_art_version") or "",
			"color_ref": doc.get("custom_color_ref") or "",
			"po_no": doc.get("custom_po_no") or "",
			"req_date": doc.get("custom_req_date"),
			"quote_no": doc.get("custom_quote_no") or "",
			"printing_machine": doc.get("custom_printing_machine") or "",
			"finishings": doc.get("custom_finishings") or "",
			"remarks": doc.get("custom_remarks") or "",
			"npd_request": doc.get("custom_npd_request") or "",
			"job_date": doc.get("posting_date"),
		},
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
