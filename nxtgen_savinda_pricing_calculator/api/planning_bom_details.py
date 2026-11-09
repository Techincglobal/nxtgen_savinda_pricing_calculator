"""Selected-BOM geometry for editable Offset and Flexo planning rows."""
import json
import math

import frappe
from frappe.utils import cint, flt

from nxtgen_savinda_pricing_calculator.api import job_ticket
from nxtgen_savinda_pricing_calculator.api.bom_builder import _library_bom_inputs, _library_sheet_size
from nxtgen_savinda_pricing_calculator.api.offset_calculator import _get_config, _flexo_wastage_settings


def _base_material(bom, preferred=None):
	seen = set()
	while bom and bom.get("items"):
		if bom.name in seen:
			frappe.throw("Circular BOM links found while resolving the planning base material.")
		seen.add(bom.name)
		row = bom.get("items")[0]
		if row.get("item_code") == preferred:
			return preferred
		if not row.get("bom_no"):
			return row.get("item_code") or ""
		bom = frappe.get_doc("BOM", row.bom_no)
	return ""


def _flexo_library_details(fg_item):
	"""Technical display fields only; these do not change planning calculations."""
	name = job_ticket._pl_name_for_fg(fg_item)
	values = frappe.db.get_value("Product Library", name,
		["cylinder_teeth", "up_margin", "core_size", "slit"], as_dict=True) if name else None
	values = values or {}
	return {
		"cylinder_teeth": str(values.get("cylinder_teeth") or ""),
		"up_margin_mm": flt(values.get("up_margin")),
		"core_size": values.get("core_size") or "",
		"slit_width": (values.get("slit") or "").strip(),
	}


def planning_row_details(fg_item, bom_no, quantity, pricing_type="Offset", planning_type="Manufacture"):
	ctx = job_ticket._fg_context(fg_item)
	bom = frappe.get_doc("BOM", bom_no) if bom_no else None
	if bom:
		if bom.get("item") != fg_item:
			frappe.throw("Select a BOM belonging to the planning row's FG Item.")
		if bom.docstatus != 1 or not bom.get("is_active"):
			frappe.throw("Select an active, submitted BOM for the planning row.")
		if bom.get("custom_pricing_type") and bom.get("custom_pricing_type") != pricing_type:
			frappe.throw("The selected BOM's pricing type does not match this Production Plan.")
	cb_name = (bom.get("custom_calculation_breakdown") if bom else None) or ctx.get("calculation_breakdown") or ""
	cb = job_ticket._cb_fields(cb_name)
	try:
		form = dict((json.loads(cb.get("ui_state") or "{}") or {}).get("form") or {})
	except (TypeError, ValueError):
		form = {}
	for field in ("no_of_ups", "no_of_cuts", "no_of_colors", "full_sheet_l", "full_sheet_w", "cut_sheet_l"):
		form.setdefault(field, cb.get(field) or 0)
	form.setdefault("cut_sheet_w", cb.get("cut_sheetw") or 0)
	form.update(_library_bom_inputs(fg_item, pricing_type))
	if bom:
		for source, target in (("custom_no_of_ups", "no_of_ups"), ("custom_no_of_cuts", "no_of_cuts"),
			("custom_reel_width_mm", "reel_width_mm"), ("custom_product_width_mm", "product_width_mm"),
			("custom_product_length_mm", "product_length_mm")):
			if flt(bom.get(source)) > 0:
				form[target] = bom.get(source)
		if bom.get("custom_calculation_breakdown") or flt(bom.get("custom_no_of_colors")) > 0:
			form["no_of_colors"] = cint(bom.get("custom_no_of_colors"))
		for source, targets in (("custom_full_sheet_size", ("full_sheet_l", "full_sheet_w")),
			("custom_cut_sheet_size", ("cut_sheet_l", "cut_sheet_w"))):
			pair = _library_sheet_size(bom.get(source))
			if pair:
				form.update(zip(targets, pair))
	qty = max(flt(quantity), 0)
	base = (bom.get("custom_base_material") or _base_material(bom, cb.get("base_material"))) if bom and planning_type != "Assembly" else ""
	out = {"item_name": frappe.db.get_value("Item", fg_item, "item_name") or fg_item,
		"cost_item": ctx.get("cost_item") or "", "calculation_breakdown": cb_name,
		"bom_no": bom_no or "", "base_material": (base or cb.get("base_material") or "") if planning_type != "Assembly" else ""}
	if pricing_type != "Flexo":
		ups, cuts = cint(form.get("no_of_ups")), max(cint(form.get("no_of_cuts")) or 1, 1)
		cut_ups = cint(bom.get("custom_cut_sheet_ups")) if bom else 0
		cut_ups = cut_ups or (max(ups // cuts, 1) if ups else 0)
		cfg = _get_config()
		cut_qty = math.ceil(qty / cut_ups) if qty and cut_ups else 0
		waste = max(math.ceil(cut_qty * flt(cfg.get("offset_wastage_pct"))), cint(cfg.get("offset_wastage_min"))) if cut_qty else 0
		out.update(ups=ups, cuts=cuts, required_cut_sheet_qty=cut_qty, cut_sheet_qty=cut_qty + waste, cut_sheet_qty_includes_wastage=1, wastage=waste,
			full_sheet_qty=math.ceil((cut_qty + waste) / cuts),
			full_sheet_size=(bom.get("custom_full_sheet_size") if bom else "") or job_ticket._size_str(form.get("full_sheet_l"), form.get("full_sheet_w")),
			cut_sheet_size=(bom.get("custom_cut_sheet_size") if bom else "") or job_ticket._size_str(form.get("cut_sheet_l"), form.get("cut_sheet_w")))
		return out
	out.update(_flexo_library_details(fg_item))
	width = flt(form.get("reel_width_mm"))
	ups = cint(bom.get("custom_no_of_ups")) if bom else 0
	colors = cint(form.get("no_of_colors"))
	pitch_width = flt(form.get("product_width_mm")) + flt(form.get("product_margin_mm", 4))
	if not ups and width > 0 and flt(form.get("product_width_mm")) > 0 and pitch_width > 0:
		ups = max(int((width - (10 if colors == 0 else 24)) /
			pitch_width), 1)
	source_qty = (flt(bom.get("custom_base_item_qty")) if bom else 0) or flt(cb.get("item_qty") or form.get("item_qty"))
	saved_length = flt(bom.get("custom_reel_length_m")) if bom else 0
	if source_qty and saved_length:
		length = saved_length * qty / source_qty
	else:
		manual = flt(form.get("reel_length_m"))
		original_qty = flt(cb.get("item_qty") or form.get("item_qty"))
		length = manual * qty / original_qty if manual and original_qty else (
			qty / ups * (flt(form.get("product_length_mm")) + flt(form.get("product_gap_mm", 3))) / 1000 if ups and flt(form.get("product_length_mm")) > 0 else 0)
	setup, pct, minimum = _flexo_wastage_settings(_get_config(), colors)
	gross_length = flt(bom.get("custom_required_reel_length_m")) if bom else 0
	# A minimum allowance is fixed metres, not a percentage to scale with order qty.
	if saved_length and gross_length >= saved_length + setup and gross_length - saved_length > minimum:
		pct = (gross_length - saved_length - setup) / saved_length
	area = (length + max(setup + length * pct, minimum)) * width / 1000 if qty and length and width else 0
	product_size = (bom.get("custom_carton_size") if bom else "") or form.get("carton_size") or cb.get("carton_size") or ""
	out.update(ups=ups, size=product_size, reel_length=(area * 1000 / width if width > 0 else 0), reel_width=width, reel_area=area)
	slit = out.get("slit_width") or (bom.get("custom_slit_width") if bom else "") or form.get("slit_width")
	if slit:
		out["slit_width"] = slit
	return out
