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
