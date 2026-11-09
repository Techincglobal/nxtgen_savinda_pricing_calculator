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
import math

import frappe
from frappe.utils import add_days, cint, flt, getdate, now, now_datetime, today

from nxtgen_savinda_pricing_calculator.api import job_ticket as jt_api
from nxtgen_savinda_pricing_calculator.api.offset_calculator import _get_config


# ── small helpers ────────────────────────────────────────────────────────────
# ── base material planning helpers ───────────────────────────────────────────
def _base_material_for_calculation(calculation_breakdown):
	"""Stock Item selected as Base Material on a saved Calculation Breakdown."""
	if not calculation_breakdown:
		return ""
	return frappe.db.get_value("Calculation Breakdown", calculation_breakdown, "base_material") or ""


def _offset_total_cut_sheets(row):
	return flt(row.get("cut_sheet_qty")) + (0 if cint(row.get("cut_sheet_qty_includes_wastage")) else flt(row.get("wastage")))


def _preserve_offset_sheet_edits(row, old, values, force=False):
	if force or not cint(row.get("cut_sheet_qty_includes_wastage")):
		return
	if old and any(row.get(k) != old.get(k) for k in ("fg_item", "bom_no", "qty")):
		return
	required, waste = flt(row.get("required_cut_sheet_qty")), flt(row.get("wastage"))
	# Direct total edit is supported: keep wastage and derive the net requirement.
	if old and row.get("cut_sheet_qty") != old.get("cut_sheet_qty") and row.get("required_cut_sheet_qty") == old.get("required_cut_sheet_qty"):
		required = flt(row.get("cut_sheet_qty")) - waste
	cuts = flt(row.get("cuts"))
	# An isolated full-sheet edit drives both cut-sheet fields in reverse.
	# Browser-calculated edits already carry their new required/total values.
	if old and row.get("full_sheet_qty") != old.get("full_sheet_qty") and all(
		row.get(k) == old.get(k) for k in ("required_cut_sheet_qty", "cut_sheet_qty")
	):
		full = flt(row.get("full_sheet_qty"))
		if not math.isfinite(full) or full < 0 or full != math.floor(full):
			frappe.throw("Full Sheet Qty must be a non-negative whole number.")
		required = full * cuts - waste
	if any(not math.isfinite(v) for v in (required, waste, cuts)) or required < 0 or waste < 0 or cuts <= 0:
		frappe.throw("Required Cut Sheets and Wastage must be non-negative, and Cuts must be greater than zero.")
	values.update(required_cut_sheet_qty=required, wastage=waste, cut_sheet_qty=required+waste,
		cuts=cuts, full_sheet_qty=math.ceil((required+waste)/cuts), cut_sheet_qty_includes_wastage=1)


def _planning_base_material_requirements(doc):
	"""Required base-material quantities from the relevant Manufacturing Planning table.

	Offset: calculate gross full sheets from Cut Sheets + Wastage / Cuts.
	Flexo: the calculated reel area already includes its reel wastage.
	"""
	pricing = doc.get("custom_pricing_type") or "Offset"
	is_flexo = pricing == "Flexo"
	field = "custom_flexo_planning" if is_flexo else "custom_offset_planning"
	required = {}
	for row in (doc.get(field) or []):
		if row.get("planning_type") == "Assembly":
			continue
		base_material = row.get("base_material") or _base_material_for_calculation(row.get("calculation_breakdown"))
		if not base_material:
			continue
		if is_flexo:
			qty = flt(row.get("reel_area"))
		else:
			cuts = max(cint(row.get("cuts")) or 1, 1)
			cut_sheets = flt(row.get("cut_sheet_qty"))
			wastage = flt(row.get("wastage"))
			qty = math.ceil(_offset_total_cut_sheets(row) / cuts) if (cut_sheets or wastage) else flt(row.get("full_sheet_qty"))
		if qty > 0:
			required[base_material] = required.get(base_material, 0) + qty
	return required


def _base_material_mr_row(item_code, quantity, template, doc):
	"""Use an ERPNext raw-material row as a template, or build a safe fallback row."""
	# Warehouse and request type are supplied by the ERPNext result/template or the
	# Production Plan.  They are not fields on Item in all ERPNext v15 versions.
	item = frappe.db.get_value("Item", item_code,
		["item_name", "description", "stock_uom", "purchase_uom"], as_dict=True) or {}
	row = dict(template or {})
	row.update({
		"item_code": item_code,
		"item_name": item.get("item_name") or item_code,
		"description": item.get("description") or row.get("description") or "",
		"stock_uom": item.get("stock_uom") or row.get("stock_uom") or "",
		"uom": row.get("uom") or item.get("purchase_uom") or item.get("stock_uom") or "",
		"warehouse": row.get("warehouse") or doc.get("for_warehouse") or "",
		"quantity": quantity,
		"required_bom_qty": quantity,
	})
	if not row.get("material_request_type"):
		row["material_request_type"] = "Purchase"
	return row


@frappe.whitelist()
def get_items_for_material_requests(doc, warehouses=None, get_parent_warehouse_data=None):
	"""ERPNext raw-material explosion plus calculator base-material requirements.

	Only Base Material rows use Manufacturing Planning quantities; all other BOM materials
	remain exactly as ERPNext calculated them.  This replacement is registered through
	override_whitelisted_methods, so ERPNext source is never modified.
	"""
	from erpnext.manufacturing.doctype.production_plan.production_plan import get_items_for_material_requests as erpnext_get_items

	rows = erpnext_get_items(doc, warehouses=warehouses, get_parent_warehouse_data=get_parent_warehouse_data) or []
	plan = frappe._dict(json.loads(doc)) if isinstance(doc, str) else frappe._dict(doc or {})
	requirements = _planning_base_material_requirements(plan)
	if not requirements:
		return rows

	# Replace each base-material result with ONE planning-driven quantity. This prevents
	# ERPNext BOM qty and the old percentage-wastage process from being added again.
	for item_code, quantity in requirements.items():
		matches = [row for row in rows if row.get("item_code") == item_code]
		rows = [row for row in rows if row.get("item_code") != item_code]
		rows.append(_base_material_mr_row(item_code, quantity, matches[0] if matches else {}, plan))
	return rows


def _fullname(user=None):
	user = user or frappe.session.user
	return frappe.db.get_value("User", user, "full_name") or user


def _require_role(role):
	roles = frappe.get_roles(frappe.session.user)
	if role not in roles and "System Manager" not in roles:
		frappe.throw("Only users with the '" + role + "' role can perform this action.")


def _first_fg_context_from_so(so, items=None):
	"""(ctx, cb, pl, pricing_type) for the first resolvable Sales Order item, else Nones."""
	for it in (items if items is not None else so.items):
		ctx = jt_api._fg_context(it.item_code)
		cb = jt_api._cb_fields(ctx.get("calculation_breakdown"))
		pl = jt_api._pl(ctx.get("product_library"))
		if cb or pl:
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
	("customer", ""), ("customer_product_code", ""), ("category", ""), ("department", ""),
	("artwork_no", ""), ("artwork_version", ""),
	("full_sheet_size", "Offset"), ("cut_sheet_size", "Offset"),
	("product_size", ""), ("pasting_type", "Offset"),
	("no_of_colors", ""), ("no_of_ups", ""),
	("proof_standard", "Offset"), ("printing_machine", "Offset"),
	("flexo_type", "Flexo"), ("width_mm", "Flexo"), ("length_mm", "Flexo"),
	("cylinder_teeth", "Flexo"), ("no_of_repeat", "Flexo"), ("repeat_gap", "Flexo"),
	("across_ups", "Flexo"), ("up_margin", "Flexo"), ("slit", "Flexo"),
	("core_size", "Flexo"), ("pcs_per_roll", "Flexo"),
	("winding_direction", "Flexo"), ("tolerance", "Flexo"), ("remark", ""),
	("foil_details", "Flexo"), ("die_cut_code", ""), ("finishings", "")
]
_PL_KEYS = [fn for fn, _ in _PL_REVIEW]

def _packing_pl_defaults(row=None, fallback=None, cost_item=None):
	"""Use the selected line's packing details, then its linked costing item."""
	if cost_item:
		fallback = frappe.db.get_value(
			"cost Item", cost_item, ["pcs_per_role", "winding_direction"], as_dict=True
		) or {}
	row, fallback = row or {}, fallback or {}
	return {
		"pl_pcs_per_roll": cint(row.get("pcs_per_role") or row.get("pcs_per_roll")
			or fallback.get("pcs_per_role") or fallback.get("pcs_per_roll") or 0),
		"pl_winding_direction": row.get("winding_direction") or fallback.get("winding_direction") or "",
	}

def _customer_from_name(customer_name):
	"""Resolve a Customer link from its document name or display name."""
	if not customer_name:
		return ""
	return (
		frappe.db.exists("Customer", customer_name)
		or frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
		or ""
	)


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
			"reqd": 1 if fn == "artwork_no" else 0,
		})
	return out


def _flexo_technical_defaults(cbd):
	"""Use explicit technical values; production repeat totals are not cylinder repeats."""
	try:
		state = json.loads(cbd.get("ui_state") or "{}")
	except (ValueError, TypeError):
		state = {}
	form = state.get("form") or {}
	sheet = (state.get("calc_result") or {}).get("sheet") or {}
	aliases = {
		"cylinder_teeth": ("cylinder_teeth", "repeat_teeth"),
		"no_of_repeat": ("no_of_repeat", "repeat_ups"),
		"repeat_gap": ("repeat_gap", "repeat_gaps", "product_gap_mm"),
		"across_ups": ("across_ups", "ups"),
		"up_margin": ("up_margin", "across_gaps", "product_margin_mm"),
		"slit": ("slit", "slit_width"),
	}
	out = {}
	for target, keys in aliases.items():
		for source in (form, sheet, cbd):
			value = next((source[k] for k in keys if source.get(k) not in (None, "")), None)
			if value is not None:
				out["pl_" + target] = value
				break
	return out


def _fg_detail_defaults(row, npd, ig, dept):
	"""Proposed Item + Product Library values for one requested line (pre-filled from the
	NPD Request line / header). Shown in the FG-creation popup for review/edit."""
	is_flexo = (npd.pricing_type or "Offset") == "Flexo"
	cbd = jt_api._cb_fields(row.get("calculation_breakdown")) if is_flexo else {}
	return {
		"row_name": row.name,
		**(_flexo_technical_defaults(cbd) if is_flexo else {}),
		"requested_name": row.item_name or "",
		# Item fields
		"item_name": (row.item_name or npd.job_title or npd.name or "").strip(),
		"item_group": ig,
		"department": dept,
		"stock_uom": row.uom or "Nos",
		"customer_ref": "",
		"cost_item": row.cost_item or "",
		# Product Library fields (pl_<fieldname>)
		"pl_customer": npd.customer or _customer_from_name(npd.customer_name),
		"pl_department": npd.pricing_type or "",
		"pl_flexo_type": "Reel" if is_flexo else "",
		"pl_customer_product_code": row.product_code or "",
		"pl_no_of_colors": int(npd.colors or 0),
		"pl_no_of_ups": int(row.ups or row.repeat_ups or 0),
		"pl_full_sheet_size": row.full_sheet_size or "",
		"pl_cut_sheet_size": row.cut_sheet_size or "",
		"pl_product_size": row.size or npd.size or "",
		"pl_width_mm": float(row.reel_width or 0),
		"pl_length_mm": flt(cbd.get("_product_length_mm")),
		"pl_artwork_no": npd.artwork_no or "",
		"pl_artwork_version": npd.artwork_version or "",
		"pl_core_size": npd.core_size or "",
		**_packing_pl_defaults(row, npd),
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
			item_name=name, description=d.get("description") or name,
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
	cs = frappe.db.get_value("Cost Sheet", cost_sheet, "customer_name") or ""
	return {
		"cost_item": ci,
		**_packing_pl_defaults(row=None, cost_item=ci),
		"item_name": ci_doc.get("cost_item_name") or ci,
		"item_group": ig,
		"department": _dept_for_pricing(pricing),
		**(_flexo_technical_defaults(cbd) if is_flexo else {}),
		"stock_uom": "Nos",
		"customer_ref": "",
		"pl_customer": _customer_from_name(cs),
		"pl_finishings": _finishing_defaults_from_cb(cb),
		"pl_department": pricing,
		"pl_flexo_type": "Reel" if is_flexo else "",
		"pl_customer_product_code": "",
		"pl_no_of_colors": cint(cbd.get("no_of_colors") or ci_doc.get("colour") or 0),
		"pl_no_of_ups": cint(cbd.get("no_of_ups") or 0),
		"pl_full_sheet_size": jt_api._size_str(cbd.get("full_sheet_l"), cbd.get("full_sheet_w")),
		"pl_cut_sheet_size": jt_api._size_str(cbd.get("cut_sheet_l"), cbd.get("cut_sheetw")),
		"pl_product_size": "",
		"pl_width_mm": flt(cbd.get("_reel_width")),
		"pl_length_mm": flt(cbd.get("_product_length_mm")),
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
		d.update(_packing_pl_defaults(row, cost_item=ci))
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
			item_name=name, description=d.get("description") or name,
			item_group=(d.get("item_group") or ig_default), department=dept,
			stock_uom=(d.get("stock_uom") or "Nos"), cost_item=ci,
			customer_ref=(d.get("customer_ref") or None), pl_overrides=pl_over,
		)
		created.append(res["item_code"])
	frappe.db.commit()
	return {"created": created, "existing": existing}


# ── Create FG on the Savinda Quotation (per cost item) ────────────────────────
def _foil_defaults_from_cb(cb_name):
	"""Product Library defaults for foil choices held in a Flexo CB's ui_state."""
	if not cb_name:
		return {"pl_cold_foil": 0, "pl_hot_foil": 0, "pl_foil_details": ""}
	try:
		state = json.loads(frappe.db.get_value("Calculation Breakdown", cb_name, "ui_state") or "{}") or {}
	except Exception:
		return {"pl_cold_foil": 0, "pl_hot_foil": 0, "pl_foil_details": ""}
	cold = hot = 0
	details, seen = [], set()
	for spec in (state.get("selected_specs") or []):
		for foil in ((spec.get("machine_assignment") or {}).get("foils") or []):
			group = (foil.get("foil_group") or "").strip().upper()
			name = (foil.get("foil_name") or foil.get("foil_key") or "").strip()
			if not name:
				continue
			cold = cold or int(group == "COLD")
			hot = hot or int(group == "HOT")
			pct = flt(foil.get("percentage") or 100)
			label = (group + ": " if group else "") + name + (" (%g%%)" % pct)
			if label not in seen:
				seen.add(label)
				details.append(label)
	return {"pl_cold_foil": cold, "pl_hot_foil": hot, "pl_foil_details": ", ".join(details)}


def _finishing_defaults_from_cb(cb):
	from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils.jinja import get_cb_finishings
	return [{"process_name": name} for name in get_cb_finishings(cb)]


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
	defaults = {
		"item_name": ci_doc.get("cost_item_name") or ci,
		**_packing_pl_defaults(cost_item=ci),
		"pl_finishings": _finishing_defaults_from_cb(cb),
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
		"pl_length_mm": flt(cbd.get("_product_length_mm")),
	}
	if is_flexo:
		defaults.update(_flexo_technical_defaults(cbd))
		defaults.update(_foil_defaults_from_cb(cb))
	return defaults


@frappe.whitelist()
def get_cost_item_fg_defaults(cost_item=None, quotation=None, quotation_item=None):
	"""Proposed FG + Product Library review values for one cost Item (quotation FG popup).
	`quotation` (optional) supplies artwork defaults from its Cost Sheet, when linked."""
	d = _pl_defaults_from_cost_item(cost_item) if cost_item and frappe.db.exists("cost Item", cost_item) else {}
	if quotation:
		frappe.get_doc("Savinda Quotation", quotation).check_permission("read")
		q = frappe.db.get_value("Savinda Quotation", quotation, ["cost_sheet", "customer", "customer_name"], as_dict=True) or {}
		d["pl_customer"] = q.get("customer") or _customer_from_name(q.get("customer_name"))
		if quotation_item:
			row = frappe.db.get_value("Savinda Quotation Item", {
				"name": quotation_item, "parent": quotation, "parenttype": "Savinda Quotation"
			}, ["pcs_per_role", "winding_direction"], as_dict=True)
			if row:
				d.update(_packing_pl_defaults(row, cost_item=cost_item))
		cs = q.get("cost_sheet")
		if cs:
			if not d.get("pricing"):
				pricing = frappe.db.get_value("Cost Sheet", cs, "pricing_type") or "Offset"
				d.update(pricing=pricing, is_flexo=pricing == "Flexo", pl_department=pricing,
					department=_dept_for_pricing(pricing))
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
def create_production_plan_from_source(source_type, source_name, selections=None):
	"""Create a DRAFT Production Plan from a Sales Order (Job) or NPD Request (NPD).
	ALL fetched items go to custom_ticket_items; only the BOM-ready ones also go to po_items
	(ERPNext requires a BOM there). Items without a BOM stay on the ticket and are flagged
	for the BOM team — no hard block, so items can be fetched before BOMs exist."""
	coverage = None
	if source_type == "Sales Order":
		from nxtgen_savinda_pricing_calculator.api.production_plan_coverage import existing_plans, order_coverage
		# Serialize this app's concurrent creation requests for the same order.
		frappe.db.sql("select name from `tabSales Order` where name=%s for update", source_name)
		so = frappe.get_doc("Sales Order", source_name)
		so.check_permission("read")
		if so.docstatus == 2:
			frappe.throw("A cancelled Sales Order cannot be planned.")
		plans = existing_plans(source_name)
		coverage = order_coverage(so, plans)
		if selections is not None:
			coverage["remaining"] = _selected_plan_quantities(so, coverage["remaining"], selections)
		readable = [plan.name for plan in plans if plan.name in coverage["existing_plans"]
			and frappe.has_permission("Production Plan", "read", doc=plan)]
		coverage["existing_plans"] = readable
		if not coverage["remaining"] and so.items:
			if not readable:
				frappe.throw("All Sales Order items are already planned. Contact a user with access to the existing Production Plans.")
			return {"production_plan": readable[0], "already_planned": True,
				"existing_production_plans": readable}
	frappe.has_permission("Production Plan", "create", throw=True)
	pp = frappe.new_doc("Production Plan")
	pp.company = jt_api._default_company()
	pp.posting_date = today()

	ticket_type, pricing_type, header, lines = _gather_ticket(source_type, source_name,
		sales_order_items=set(coverage["remaining"]) if coverage else None,
		quantities=coverage["remaining"] if coverage else None)
	if not lines:
		frappe.throw("No items found on the selected " + str(source_type) + ".")

	for tl in lines:
		pp.append("custom_ticket_items", tl)
	# po_items: native ERPNext pull from the Sales Order so the standard Production Plan →
	# Work Order flow runs; the bespoke builder is only used for the dormant NPD Request source.
	if source_type == "Sales Order":
		_populate_po_items_from_so(pp, source_name, quantities=coverage["remaining"])
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
	return {"production_plan": pp.name, "pending_bom": pending, "needs_bom": bool(pending),
		"existing_production_plans": coverage["existing_plans"] if coverage else [],
		"already_planned": False}


@frappe.whitelist()
def create_plan_from_npd(npd_request):
	return create_production_plan_from_source("NPD Request", npd_request)


def _selected_plan_quantities(so, remaining, selections):
	"""Validate row identities and quantities against current coverage under the SO lock."""
	if isinstance(selections, str):
		selections = frappe.parse_json(selections)
	if not isinstance(selections, list) or not selections:
		frappe.throw("Select at least one available Sales Order item.")
	selected = {}
	valid = {row.name for row in so.items}
	for row in selections:
		if not isinstance(row, dict):
			frappe.throw("Invalid Sales Order item selection.")
		key = row.get("sales_order_item")
		qty = flt(row.get("qty"))
		if key not in valid or key in selected:
			frappe.throw("Selected Sales Order rows must belong to this order and cannot repeat.")
		if key not in remaining or not math.isfinite(qty) or qty <= 0 or qty > remaining[key] + 0.000001:
			frappe.throw("Selected quantity exceeds the available quantity or is invalid. Reload the item list.")
		selected[key] = min(qty, remaining[key])
	return selected


@frappe.whitelist()
def get_sales_order_planning_items(sales_order):
	from nxtgen_savinda_pricing_calculator.api.production_plan_coverage import existing_plans, order_coverage
	so = frappe.get_doc("Sales Order", sales_order)
	so.check_permission("read")
	frappe.has_permission("Production Plan", "create", throw=True)
	if so.docstatus == 2:
		frappe.throw("A cancelled Sales Order cannot be planned.")
	plans = existing_plans(so.name)
	coverage = order_coverage(so, plans)
	items = [{"sales_order_item": row.name, "item_code": row.item_code,
		"item_name": row.get("item_name") or row.item_code,
		"gang_id": (row.get("custom_gang_id") or "").strip(),
		"order_qty": flt(row.qty), "planned_qty": coverage["planned"].get(row.name, 0),
		"qty": coverage["remaining"][row.name], "uom": row.get("uom") or ""}
		for row in so.items if row.name in coverage["remaining"]]
	return {"items": items, "gang_ids": sorted({row["gang_id"] for row in items if row["gang_id"]}),
		"existing_production_plans": [plan.name for plan in plans if plan.name in coverage["existing_plans"]
			and frappe.has_permission("Production Plan", "read", doc=plan)]}


@frappe.whitelist()
def create_plan_from_sales_order(sales_order, selections=None, plan_mode="combined"):
	# Preserve the existing API for callers that do not supply a selection.
	if selections is None:
		return create_production_plan_from_source("Sales Order", sales_order)
	if plan_mode not in ("combined", "individual"):
		frappe.throw("Choose one combined Production Plan or one per selected item.")
	from nxtgen_savinda_pricing_calculator.api.production_plan_coverage import existing_plans, order_coverage
	frappe.db.sql("select name from `tabSales Order` where name=%s for update", sales_order)
	so = frappe.get_doc("Sales Order", sales_order)
	so.check_permission("read")
	if so.docstatus == 2:
		frappe.throw("A cancelled Sales Order cannot be planned.")
	frappe.has_permission("Production Plan", "create", throw=True)
	coverage = order_coverage(so, existing_plans(sales_order))
	quantities = _selected_plan_quantities(so, coverage["remaining"], selections)
	selected = [{"sales_order_item": key, "qty": qty} for key, qty in quantities.items()]
	groups = [selected] if plan_mode == "combined" else [[row] for row in selected]
	results = [create_production_plan_from_source("Sales Order", sales_order, selections=group) for group in groups]
	return {"production_plan": results[0]["production_plan"],
		"production_plans": [result["production_plan"] for result in results],
		"already_planned": False}


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


def _sales_order_quotation_ref(so):
	if so.get("custom_savinda_quotation"):
		return so.custom_savinda_quotation
	return ", ".join(dict.fromkeys(row.get("prevdoc_docname") for row in so.items if row.get("prevdoc_docname")))


def _gather_ticket(source_type, source_name, sales_order_items=None, quantities=None):
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
		library = jt_api._pl(jt_api._pl_name_for_fg(lines[0]["fg_item"])) if lines else {}
		header = {
			"custom_npd_request": src.name, "custom_customer": src.customer,
			"custom_customer_name": src.customer_name, "custom_colors": cint(src.colors),
			"custom_job_title": src.job_title, "custom_material": src.material,
			"custom_job_board": src.material, "custom_finishings": src.finishings,
			"custom_color_ref": src.color_ref, "custom_quote_no": src.quote_no,
			"custom_remarks": src.remarks, "custom_art_no": library.get("artwork_no") or src.artwork_no,
			"custom_art_version": library.get("artwork_version") or src.artwork_version,
			# Artwork must be approved in this PP workflow, independently of NPD status.
			"custom_artwork_status": "Pending",
		}
		return "NPD", pricing, header, lines

	if source_type == "Sales Order":
		so = frappe.get_doc("Sales Order", source_name)
		items = [row for row in so.items if sales_order_items is None or row.name in sales_order_items]
		ctx, cb, pl, pricing = _first_fg_context_from_so(so, items=items)
		lines = []
		for it in items:
			qty = (quantities or {}).get(it.name, it.qty)
			fctx = jt_api._fg_context(it.item_code)
			fcb = jt_api._cb_fields(fctx.get("calculation_breakdown"))
			fpl = jt_api._pl(fctx.get("product_library"))
			pr = (fpl.get("department") if fpl else "") or fcb.get("pricing_type") or pricing
			geom = jt_api._item_line_from_cb(it.item_name or it.item_code, qty, fctx, fcb, fpl, pr)
			bom = _resolve_bom_no(it.item_code)
			line = _ticket_line(it.item_code, qty, geom, bom)
			line.update(sales_order=so.name, sales_order_item=it.name)
			lines.append(line)
		if cb.get("base_material"):
			board = frappe.db.get_value("Item", cb["base_material"], "item_name") or cb["base_material"]
		else:
			board = cb.get("custom_material_name") or ""
		header = {
			"custom_sales_order": so.name,
			"custom_department": so.get("custom_department") or "",
			"custom_customer": so.customer, "custom_customer_name": so.customer_name,
			"custom_po_no": so.po_no,
			"custom_colors": cint(cb.get("no_of_colors")), "custom_job_board": board,
			"custom_material": board,
			"custom_job_title": (pl.get("product_name") if pl else "") or so.customer_name,
			"custom_art_no": (pl.get("artwork_no") if pl else "") or "",
			"custom_art_version": (pl.get("artwork_version") if pl else "") or "",
			"custom_quote_no": _sales_order_quotation_ref(so),
			"custom_finishings": jt_api._finishings_text(pl) if pl else "",
			# The CS Team approves artwork on the PP before it can be submitted.
			"custom_artwork_status": "Pending",
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
		factor = (frappe.db.get_value("Sales Order Item", tl.sales_order_item, "conversion_factor")
			if tl.get("sales_order_item") else 1) or 1
		planned_qty = (flt(tl.qty) or 1) * flt(factor)
		po = {
			"item_code": tl.fg_item, "bom_no": tl.bom_no,
			"planned_qty": planned_qty, "pending_qty": planned_qty,
			"stock_uom": stock_uom, "warehouse": fg_wh, "planned_start_date": now_datetime(),
			"sales_order": tl.get("sales_order"), "sales_order_item": tl.get("sales_order_item"),
		}
		po.update(_po_row_from_geom({f: tl.get(f) for f in _TICKET_GEOM_FIELDS}))
		pp.append("po_items", po)


def _populate_po_items_from_so(pp, so_name, quantities=None):
	"""Populate po_items natively from a Sales Order (ERPNext ProductionPlan.get_items), so
	the standard Production Plan → Work Order pipeline runs. Sets get_items_from + the
	sales_orders reference, then falls back to the manual builder if the native pull yields
	nothing (e.g. FGs without a default BOM)."""
	so = frappe.get_doc("Sales Order", so_name)
	pp.get_items_from = "Sales Order"
	if quantities is not None:
		pp.combine_items = 0  # Preserve the Sales Order row links for partial planning.
	if not any((r.get("sales_order") == so_name) for r in (pp.get("sales_orders") or [])):
		pp.append("sales_orders", {
			"sales_order": so.name,
			"sales_order_date": so.transaction_date,
			"customer": so.customer,
			"grand_total": flt(so.get("base_grand_total") or so.get("grand_total")),
		})
	try:
		pp._pricing_source_initialization = True
		pp.get_items()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "PP native get_items from SO failed")
	finally:
		pp._pricing_source_initialization = False
	if not (pp.get("po_items") or []):
		_build_po_items(pp)
	if quantities is not None:
		factors = {row.name: flt(row.get("conversion_factor")) or 1 for row in so.items}
		selected = []
		for row in pp.get("po_items") or []:
			if row.get("sales_order_item") not in quantities:
				continue
			row.planned_qty = quantities[row.sales_order_item] * factors[row.sales_order_item]
			row.pending_qty = row.planned_qty
			row.idx = len(selected) + 1
			selected.append(row)
		pp.set("po_items", selected)


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
	quantities = {}
	if source_type == "Sales Order":
		for row in pp.get("custom_ticket_items") or []:
			if row.get("sales_order_item") and row.get("sales_order") in (None, "", source_name):
				key = row.sales_order_item
				quantities[key] = quantities.get(key, 0) + flt(row.qty)
	ticket_type, pricing_type, header, lines = _gather_ticket(source_type, source_name,
		sales_order_items=set(quantities) if quantities else None, quantities=quantities or None)

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


def _sync_pp_required_date(doc, require_date=False):
	"""CS enters the PP date; expected dates follow it, independently of expiry."""
	if not doc.get("custom_ticket_type"):
		return
	# "Approved" is the draft state reached by Validate Stock, not submission.
	final_approval = doc.get("workflow_state") == "Submitted" or doc.get("docstatus") == 1
	required_date = doc.get("custom_req_date")
	if (require_date or final_approval) and not required_date:
		frappe.throw("Set Required Date on the Production Plan before submitting the plan.",
			title="Required Date Missing")
	if not required_date:
		return
	for table, item_field, date_field in (("custom_ticket_items", "fg_item", "exp_date"),
		("custom_offset_planning", "fg_item", "exp_date"), ("custom_flexo_planning", "fg_item", "exp_date"),
		("po_items", "item_code", "custom_exp_date")):
		for row in (doc.get(table) or []):
			if row.get(item_field):
				row.set(date_field, required_date)


def validate_ticket_item_exp_dates(doc, method=None):
	"""On submit, require an Expected Date on every Job Ticket item row. Scoped to our plans
	(custom_ticket_type set) so standard Production Plans are untouched."""
	if not doc.get("custom_ticket_type"):
		return
	_sync_pp_required_date(doc, require_date=True)
	missing = []
	for r in (doc.get("custom_ticket_items") or []):
		if not r.get("fg_item"):
			continue
		if not r.get("exp_date"):
			missing.append(r.get("description") or frappe.db.get_value("Item", r.fg_item, "item_name")
			               or r.fg_item or ("Row %s" % r.idx))
	if missing:
		frappe.throw(
			"Set the <b>Expected Date</b> on all Job Ticket items before submitting.<br>Missing on: "
			+ ", ".join("<b>%s</b>" % frappe.utils.escape_html(m) for m in missing),
			title="Expected Date Required")


def _refresh_manual_planning_rows(doc, force=False):
	"""Recalculate Offset/Flexo Manufacturing Planning rows before a draft plan is saved.
	This makes manual FG, qty and BOM edits use the current item/BOM calculation data."""
	if doc.docstatus != 0:
		return
	from nxtgen_savinda_pricing_calculator.api.planning_bom_details import planning_row_details
	pricing = doc.get("custom_pricing_type") or "Offset"
	field = "custom_flexo_planning" if pricing == "Flexo" else "custom_offset_planning"
	previous = doc.get_doc_before_save() if callable(getattr(doc, "get_doc_before_save", None)) else None
	old_rows = {r.name: r for r in (previous.get(field) or [])} if previous else {}
	selected = {}
	for row in doc.get(field) or []:
		fg = row.get("fg_item")
		if not fg or not frappe.db.exists("Item", fg):
			continue
		bom = row.get("bom_no") or _active_bom_for_item(fg)
		values = planning_row_details(fg, bom, row.get("qty"), pricing, row.get("planning_type") or "Manufacture")
		if pricing != "Flexo":
			_preserve_offset_sheet_edits(row, old_rows.get(row.get("name")), values, force)
		if pricing == "Flexo":
			old = old_rows.get(row.get("name"))
			# Keep a reviewed area/width on normal saves. Driver changes still recompute.
			if not force and ((old and all(row.get(k) == old.get(k) for k in ("fg_item", "bom_no", "qty")))
				or (not old and flt(row.get("reel_area")) > 0 and flt(row.get("reel_width")) > 0)):
				for k in ("reel_area", "reel_width"):
					if row.get(k) is not None:
						values[k] = flt(row.get(k))
			width = flt(values.get("reel_width"))
			values["reel_length"] = flt(values.get("reel_area")) * 1000 / width if width > 0 else 0
		for key, value in values.items():
			row.set(key, value)
		if bom:
			selected.setdefault(fg, set()).add(bom)
	# A popup-consolidated run keeps one set of sheet requirements after normal
	# row refresh. Other row-specific fields (cuts, ups, sizes, BOM) stay intact.
	if pricing != "Flexo" and doc.get("custom_ticket_type") != "NPD":
		group = [row for row in doc.get(field) or [] if cint(row.get("consolidated_run"))]
		if group:
			first = group[0]
			values = planning_row_details(first.get("fg_item"), first.get("bom_no") or _active_bom_for_item(first.get("fg_item")),
				sum(flt(row.get("qty")) for row in group), pricing, first.get("planning_type") or "Manufacture")
			_preserve_offset_sheet_edits(first, old_rows.get(first.get("name")), values, force or any(
				row.get("qty") != (old_rows.get(row.get("name")) or {}).get("qty") for row in group))
			for i, row in enumerate(group):
				row.set("is_first", 1 if i == 0 else 0)
				for key in ("full_sheet_qty", "required_cut_sheet_qty", "cut_sheet_qty", "wastage"):
					row.set(key, flt(values.get(key)) if i == 0 else 0)
	# Keep manufacturing references aligned when the FG has one selected BOM.
	# Several run-specific BOMs must not be collapsed into one arbitrary reference.
	for fg, boms in selected.items():
		if len(boms) != 1:
			continue
		bom = next(iter(boms))
		for row in doc.get("custom_ticket_items") or []:
			if row.get("fg_item") == fg:
				row.bom_no, row.has_bom = bom, 1
		for row in doc.get("po_items") or []:
			if row.get("item_code") == fg:
				row.bom_no = bom


@frappe.whitelist()
def get_planning_row_details(fg_item, qty, bom_no=None, pricing_type="Offset", planning_type="Manufacture"):
	from nxtgen_savinda_pricing_calculator.api.planning_bom_details import planning_row_details
	frappe.get_doc("Item", fg_item).check_permission("read")
	bom_no = bom_no or _active_bom_for_item(fg_item)
	if bom_no:
		frappe.get_doc("BOM", bom_no).check_permission("read")
	return planning_row_details(fg_item, bom_no, qty, pricing_type, planning_type)


@frappe.whitelist()
def refresh_planning_calculations(production_plan):
	"""Save a draft plan after refreshing every manual planning row."""
	pp = frappe.get_doc("Production Plan", production_plan)
	if pp.docstatus != 0:
		frappe.throw("Planning calculations can only be refreshed while the Production Plan is a draft.")
	_refresh_manual_planning_rows(pp, force=True)
	pp.save(ignore_permissions=True)
	return {"rows": len(pp.get("custom_flexo_planning" if (pp.get("custom_pricing_type") == "Flexo") else "custom_offset_planning") or [])}


def _pp_specification_fgs(doc):
	for table, key in (("custom_ticket_items", "fg_item"), ("po_items", "item_code"),
		("custom_flexo_planning" if doc.get("custom_pricing_type") == "Flexo" else "custom_offset_planning", "fg_item")):
		items = list(dict.fromkeys(row.get(key) for row in (doc.get(table) or []) if row.get(key)))
		if items:
			return items
	return []


def _pp_library_specifications(fg_item):
	pl_name = jt_api._pl_name_for_fg(fg_item)
	if not pl_name:
		return {}
	pl = frappe.db.get_value("Product Library", pl_name,
		["printing_machine", "winding_direction", "pcs_per_roll", "core_size", "artwork_no", "artwork_version"], as_dict=True) or {}
	values = {"custom_winding_direction": pl.get("winding_direction") or "",
		"custom_pcs_per_roll": cint(pl.get("pcs_per_roll")), "custom_core_size": pl.get("core_size") or "",
		"custom_art_no": pl.get("artwork_no") or "", "custom_art_version": pl.get("artwork_version") or ""}
	machine = pl.get("printing_machine")
	if machine:
		workstation = frappe.db.exists("Workstation", machine)
		if not workstation:
			workstation = frappe.db.get_value("Offset Machine", machine, "workstation")
		if workstation and frappe.db.exists("Workstation", workstation):
			values["custom_workstation"] = workstation
	return {field: value for field, value in values.items() if value not in (None, "", 0)}


@frappe.whitelist()
def get_pp_library_specifications(fg_item):
	frappe.get_doc("Item", fg_item).check_permission("read")
	pl_name = jt_api._pl_name_for_fg(fg_item)
	if pl_name:
		frappe.get_doc("Product Library", pl_name).check_permission("read")
	return _pp_library_specifications(fg_item)


def _fill_pp_library_specifications(doc):
	if doc.docstatus != 0:
		return
	items = _pp_specification_fgs(doc)
	if len(items) != 1:
		return  # Mixed FGs require an explicit choice in the client fetch action.
	for field, value in _pp_library_specifications(items[0]).items():
		if field not in ("custom_workstation", "custom_art_no", "custom_art_version") and doc.get("custom_pricing_type") != "Flexo":
			continue
		if not doc.get(field) and value not in (None, "", 0):
			doc.set(field, value)


def on_production_plan_before_save(doc, method=None):
	"""Auto-fetch Job Ticket header + item list as soon as a source (SO / NPD) is set."""
	try:
		_populate_pp_ticket_fields(doc, force=False)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "production_plan auto-populate failed")
	# Required-date validation must propagate to stop the workflow transition.
	_sync_pp_required_date(doc)
	try:
		_fill_pp_library_specifications(doc)
		_stamp_planning_line_fields(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "production_plan stamp planning line fields failed")
	_refresh_manual_planning_rows(doc)


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


@frappe.whitelist()
def split_job_ticket_item(production_plan, ticket_item, first_qty):
	"""Split one draft Job Ticket item into two rows without losing its FG/BOM/geometry.

	Example: 1675 split at 1600 becomes two otherwise identical rows: 1600 and 75.
	The native Production Plan rows are rebuilt too, so subsequent manufacturing planning
	and Work Order creation use the same breakdown.
	"""
	roles = frappe.get_roles(frappe.session.user)
	if "CS Team" not in roles and "System Manager" not in roles:
		frappe.throw("Only CS Team users can split Job Ticket items.")
	pp = frappe.get_doc("Production Plan", production_plan)
	if pp.docstatus != 0:
		frappe.throw("Job Ticket items can only be split while the Production Plan is a draft.")
	row = next((r for r in (pp.get("custom_ticket_items") or []) if r.name == ticket_item), None)
	if not row:
		frappe.throw("Job Ticket item was not found on this Production Plan.")
	original = flt(row.qty)
	first_qty = flt(first_qty)
	if first_qty <= 0 or first_qty >= original:
		frappe.throw("Split quantity must be greater than zero and less than the current quantity ({0}).".format(original))
	second_qty = original - first_qty
	copy = row.as_dict().copy()
	for key in ("name", "idx", "parent", "parentfield", "parenttype", "doctype", "docstatus", "owner", "creation", "modified", "modified_by"):
		copy.pop(key, None)
	row.qty = first_qty
	copy["qty"] = second_qty
	pp.append("custom_ticket_items", copy)
	_build_po_items(pp)
	pp.save(ignore_permissions=True)
	return {"first_qty": first_qty, "second_qty": second_qty, "rows": len(pp.custom_ticket_items or [])}


# ── Manufacturing planning: "Get Finished Goods for Manufacture" ─────────────
@frappe.whitelist()
def get_manufacture_fg_list(production_plan):
	"""Job Ticket lines (with their own qty + cost links) for the manufacturing dialog.

	Do not deduplicate by FG: a manually split Job Ticket item must be shown as two selectable
	lines (for example, 1600 and 75), even though both lines use the same FG and calculation.
	"""
	pp = frappe.get_doc("Production Plan", production_plan)
	out = []
	for tl in (pp.get("custom_ticket_items") or []):
		line = {
			"fg_item": tl.get("fg_item") or "",
			"item_name": tl.get("description") or tl.get("fg_item") or "",
			"cost_item": tl.get("cost_item") or "",
			"calculation_breakdown": tl.get("calculation_breakdown") or "",
			"qty": flt(tl.get("qty")) or 1,
		}
		for field in _TICKET_GEOM_FIELDS:
			line[field] = tl.get(field)
		line.update({"batch_no": tl.get("batch_no"), "pack_date": tl.get("pack_date"), "exp_date": tl.get("exp_date")})
		out.append(line)
	if not out:
		# Native plan without ticket items — fall back to the source lines.
		source_type, source_name = _resolve_source(pp)
		if source_type:
			_ttype, _pr, _hdr, lines = _gather_ticket(source_type, source_name)
			for tl in lines:
				line = {
					"fg_item": tl.get("fg_item") or "",
					"item_name": tl.get("description") or tl.get("fg_item") or "",
					"cost_item": tl.get("cost_item") or "",
					"calculation_breakdown": tl.get("calculation_breakdown") or "",
					"qty": flt(tl.get("qty")) or 1,
				}
				for field in _TICKET_GEOM_FIELDS:
					line[field] = tl.get(field)
				line.update({"batch_no": tl.get("batch_no"), "pack_date": tl.get("pack_date"), "exp_date": tl.get("exp_date")})
				out.append(line)
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
	out = {"ups": 0, "cuts": 0, "full_sheet_qty": 0.0, "cut_sheet_qty": 0.0, "wastage": 0.0, "reel_area": 0.0, "reel_length": 0.0, "reel_width": 0.0, "slit_width": ""}
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
		out["reel_length"] = flt(sheet.get("reel_length") or form.get("reel_length") or form.get("material_length"))
		out["reel_width"] = flt(sheet.get("reel_width") or form.get("reel_width") or form.get("material_width"))
		out["slit_width"] = sheet.get("slit_width") or form.get("slit_width") or ""
	else:
		out["ups"] = cint(form.get("no_of_ups"))
		out["full_sheet_qty"] = flt(sheet.get("full_sheet_qty"))
		out["cut_sheet_qty"] = flt(sheet.get("cut_sheet_qty"))
		out["wastage"] = flt(sheet.get("wastage"))
		out["full_sheet_size"] = f"{flt(form.get('full_sheet_l')):g} x {flt(form.get('full_sheet_w')):g}"
		out["cut_sheet_size"] = f"{flt(form.get('cut_sheet_l')):g} x {flt(form.get('cut_sheetw') or form.get('cut_sheet_w')):g}"
	return out


@frappe.whitelist()
def add_planning_items(production_plan, selections, consolidate=0):
	"""Add selected FGs to the Offset/Flexo planning table with print data computed from
	each item's cost calculation at the (possibly edited) qty.

	consolidate (Offset only): the selection is ONE combined print run — full/cut sheet qty
	and wastage are computed for the whole group and put
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
			"base_material": _base_material_for_calculation(cb),
			"qty": qty,
			"ups": pd["ups"], "cuts": pd["cuts"],
		}
		if is_flexo:
			row.update({
				"size": sel.get("size") or "", "pack_date": sel.get("pack_date"),
				"exp_date": sel.get("exp_date"), "batch_no": sel.get("batch_no") or "",
				"product_code": sel.get("product_code") or "",
				"reel_length": flt(sel.get("reel_length")) or pd["reel_length"],
				"reel_width": flt(sel.get("reel_width")) or pd["reel_width"],
				"reel_area": pd["reel_area"] or flt(sel.get("reel_area")),
				"slit_width": sel.get("slit_width") or pd["slit_width"],
			})
		elif npd_mode:
			pass  # NPD sample: ups & cuts only, no sheet qty.
		elif consolidate:
			row["consolidated_run"] = 1
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


def _active_bom_for_item(item_code):
	return (
		frappe.db.get_value("BOM", {"item": item_code, "is_active": 1, "docstatus": 1, "is_default": 1}, "name")
		or frappe.db.get_value("BOM", {"item": item_code, "is_active": 1, "docstatus": 1}, "name", order_by="modified desc")
		or ""
	)


def _bom_manufacturing_components(item_code, required_qty):
	"""Return direct, first-level BOM children that are manufactured sub-assemblies."""
	bom_name = _active_bom_for_item(item_code)
	if not bom_name:
		return []
	bom_qty = flt(frappe.db.get_value("BOM", bom_name, "quantity")) or 1
	rows = []
	for part in frappe.get_all("BOM Item", filters={"parent": bom_name}, fields=["item_code", "qty", "conversion_factor"], order_by="idx asc"):
		child_bom = _active_bom_for_item(part.item_code)
		if not child_bom:
			continue
		child_qty = flt(required_qty) * flt(part.qty) * (flt(part.conversion_factor) or 1) / bom_qty
		rows.append({"item_code": part.item_code, "qty": child_qty, "bom_no": child_bom})
	return rows


@frappe.whitelist()
def add_bom_items_for_manufacture(production_plan):
	"""Replace the planning table with SFG manufacture rows and final assembly rows."""
	pp = frappe.get_doc("Production Plan", production_plan)
	pp.check_permission("write")
	if pp.docstatus != 0:
		frappe.throw("BOM manufacturing items can only be prepared while the plan is a draft.")
	pricing = pp.get("custom_pricing_type") or "Offset"
	is_flexo = pricing == "Flexo"
	field = "custom_flexo_planning" if is_flexo else "custom_offset_planning"
	rows = []
	for ticket in pp.get("custom_ticket_items") or []:
		fg, qty = ticket.get("fg_item"), flt(ticket.get("qty")) or 1
		bundle_source = frappe.db.get_value("Item", fg, "custom_costing_bundle") if fg else None
		bundle = frappe.get_doc("Cost Sheet", bundle_source) if bundle_source else None
		if bundle:
			from nxtgen_savinda_pricing_calculator.api.costing_bundle import validate_assembly_bom
			validate_assembly_bom(bundle, _active_bom_for_item(fg))
			if bundle.pricing_type != pricing:
				frappe.throw("Production Plan type must match the bundle Cost Sheet's production type.")
		components = _bom_manufacturing_components(fg, qty)
		if bundle and not {r.fg_item for r in bundle.custom_bundle_components}.issubset({r["item_code"] for r in components}):
			frappe.throw("Every bundle component needs its own active BOM before manufacturing planning.")
		if bundle and any(not frappe.db.exists("BOM", {"name": r["bom_no"], "docstatus": 1, "is_active": 1}) for r in components):
			frappe.throw("Submit the active BOM of each manufactured bundle component before planning.")
		if components:
			rows.append({"fg_item": fg, "item_name": ticket.get("description") or fg, "qty": qty,
				"planning_type": "Assembly", "bom_no": _active_bom_for_item(fg), "is_group": 1 if bundle and not is_flexo else 0})
		else:
			components = [{"item_code": fg, "qty": qty, "bom_no": _active_bom_for_item(fg)}]
		for component in components:
			item_code, component_qty = component["item_code"], component["qty"]
			ctx = jt_api._fg_context(item_code)
			cb = ctx.get("calculation_breakdown") or ""
			if bundle:
				cb = next((r.calculation_breakdown for r in bundle.custom_bundle_components if r.fg_item == item_code), cb)
			pd = _plan_print_data(cb, component_qty, pricing)
			row = {"fg_item": item_code, "item_name": frappe.db.get_value("Item", item_code, "item_name") or item_code,
				"qty": component_qty, "planning_type": "Manufacture", "bom_no": component["bom_no"],
				"cost_item": ctx.get("cost_item") or "", "calculation_breakdown": cb,
				"base_material": _base_material_for_calculation(cb),
				"ups": pd["ups"], "cuts": pd["cuts"],
				"full_sheet_size": pd.get("full_sheet_size") or "", "cut_sheet_size": pd.get("cut_sheet_size") or ""}
			if bundle and not is_flexo:
				row.update(is_group=1, parent_item=fg)
			if is_flexo:
				row.update({"reel_length": pd["reel_length"], "reel_width": pd["reel_width"],
					"reel_area": pd["reel_area"], "slit_width": pd["slit_width"]})
			else:
				row.update({"full_sheet_qty": pd["full_sheet_qty"], "cut_sheet_qty": pd["cut_sheet_qty"], "wastage": pd["wastage"]})
			rows.append(row)
	pp.set(field, [])
	for row in rows:
		pp.append(field, row)
	pp.save(ignore_permissions=True)
	return {"added": len(rows), "assembly_rows": sum(1 for row in rows if row.get("planning_type") == "Assembly")}


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
		"size": t.get("size"), "pack_date": t.get("pack_date"), "exp_date": t.get("exp_date"),
		"batch_no": t.get("batch_no"), "product_code": t.get("product_code"),
		"reel_length": t.get("reel_length"), "reel_width": t.get("reel_width"),
		"reel_area": t.get("reel_area"), "slit_width": t.get("slit_width"),
	} for t in tickets]
	try:
		add_planning_items(pp.name, selections, consolidate=0)
		return True
	except Exception:
		frappe.log_error(frappe.get_traceback(), "auto _ensure_planning failed")
		return False


# ── Post-submit base-material requests ──────────────────────────────────────
def _submitted_ticket_plan(production_plan):
	pp = frappe.get_doc("Production Plan", production_plan)
	if pp.docstatus != 1:
		frappe.throw("Submit the Production Plan before creating a Base Material Request.")
	return pp


def _has_submitted_material_request(pp):
	"""A re-request is permitted only after the initial ERPNext request was submitted."""
	parents = frappe.get_all(
		"Material Request Item", filters={"production_plan": pp.name}, pluck="parent", distinct=True,
	)
	return bool(parents and frappe.db.exists("Material Request", {"name": ["in", parents], "docstatus": 1}))


def _base_material_request_rows(pp):
	"""Manufacture rows that a user may request after the Production Plan is submitted."""
	pricing = pp.get("custom_pricing_type") or "Offset"
	field = "custom_flexo_planning" if pricing == "Flexo" else "custom_offset_planning"
	rows = []
	for row in pp.get(field) or []:
		if row.get("planning_type") == "Assembly":
			continue
		base_material = row.get("base_material") or _base_material_for_calculation(row.get("calculation_breakdown"))
		if not base_material or flt(row.get("qty")) <= 0:
			continue
		rows.append({
			"planning_row": row.name,
			"fg_item": row.get("fg_item"),
			"item_name": row.get("item_name") or row.get("fg_item"),
			"planned_qty": flt(row.get("qty")),
			"base_material": base_material,
			"base_material_name": frappe.db.get_value("Item", base_material, "item_name") or base_material,
		})
	return rows


def _base_material_request_preview(pp, selections):
	"""Calculate requestable base materials from user-entered FG quantities."""
	if isinstance(selections, str):
		selections = frappe.parse_json(selections) or []
	if not selections:
		frappe.throw("Select at least one Manufacture item and enter a quantity.")

	pricing = pp.get("custom_pricing_type") or "Offset"
	is_flexo = pricing == "Flexo"
	field = "custom_flexo_planning" if is_flexo else "custom_offset_planning"
	planning_rows = {row.name: row for row in (pp.get(field) or [])}
	materials = {}
	for selected in selections:
		row = planning_rows.get(selected.get("planning_row"))
		if not row or row.get("planning_type") == "Assembly":
			frappe.throw("One selected Manufacturing Planning row is no longer valid.")
		fg_qty = flt(selected.get("fg_qty"))
		planned_qty = flt(row.get("qty"))
		if fg_qty <= 0 or fg_qty > planned_qty:
			frappe.throw("Manufacture quantity for {0} must be greater than zero and cannot exceed {1}.".format(
				row.get("fg_item") or "the item", planned_qty))
		base_material = row.get("base_material") or _base_material_for_calculation(row.get("calculation_breakdown"))
		if not base_material:
			frappe.throw("Set Base Material on Manufacturing Planning row {0}.".format(row.idx))
		factor = fg_qty / planned_qty
		if is_flexo:
			qty = flt(row.get("reel_area")) * factor
		else:
			cuts = max(cint(row.get("cuts")) or 1, 1)
			qty = math.ceil((_offset_total_cut_sheets(row) * factor) / cuts)
			if not qty:
				qty = math.ceil(flt(row.get("full_sheet_qty")) * factor)
		# Older submitted plans can predate the planning refresh and therefore have
		# zero print figures. Rebuild only that row from its linked calculation so a
		# valid request can still be created; current plans use the table values above.
		if not qty and row.get("calculation_breakdown"):
			data = _plan_print_data(row.get("calculation_breakdown"), fg_qty, pricing)
			if is_flexo:
				qty = flt(data.get("reel_area"))
			else:
				data_cuts = max(cint(data.get("cuts")) or 1, 1)
				qty = math.ceil((flt(data.get("cut_sheet_qty")) + flt(data.get("wastage"))) / data_cuts)
				if not qty:
					qty = math.ceil(flt(data.get("full_sheet_qty")))
		if qty <= 0:
			frappe.throw("No base material quantity could be calculated for {0}.".format(row.get("fg_item")))
		entry = materials.setdefault(base_material, {
			"item_code": base_material,
			"item_name": frappe.db.get_value("Item", base_material, "item_name") or base_material,
			"calculated_qty": 0.0,
			"uom": frappe.db.get_value("Item", base_material, "stock_uom") or "",
		})
		entry["calculated_qty"] += qty

	for entry in materials.values():
		entry["calculated_qty"] = round(entry["calculated_qty"], 6)
	return list(materials.values())


@frappe.whitelist()
def get_base_material_request_rows(production_plan):
	pp = _submitted_ticket_plan(production_plan)
	if not _has_submitted_material_request(pp):
		frappe.throw("Submit the initial Material Request created through ERPNext before making a Base Material Re-Request.")
	return _base_material_request_rows(pp)


@frappe.whitelist()
def can_re_request_base_material(production_plan):
	return _has_submitted_material_request(_submitted_ticket_plan(production_plan))


@frappe.whitelist()
def preview_base_material_request(production_plan, selections):
	pp = _submitted_ticket_plan(production_plan)
	if not _has_submitted_material_request(pp):
		frappe.throw("Submit the initial Material Request before making a Base Material Re-Request.")
	return _base_material_request_preview(pp, selections)


@frappe.whitelist()
def create_base_material_request(production_plan, selections, request_items):
	"""Create a draft Material Transfer request from approved post-submit quantities."""
	pp = _submitted_ticket_plan(production_plan)
	if not _has_submitted_material_request(pp):
		frappe.throw("Submit the initial Material Request before making a Base Material Re-Request.")
	calculated = _base_material_request_preview(pp, selections)
	if isinstance(request_items, str):
		request_items = frappe.parse_json(request_items) or []
	requested_by_item = {row.get("item_code"): flt(row.get("qty")) for row in request_items if row.get("item_code")}
	if set(requested_by_item) != {row["item_code"] for row in calculated}:
		frappe.throw("Base Material Request items changed. Preview the request again before confirming.")

	# Keep warehouse choices consistent with standard Get Raw Materials results.
	standard_rows = {row.get("item_code"): row for row in (pp.get("mr_items") or [])}
	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Material Transfer"
	mr.company = pp.company
	mr.transaction_date = today()
	# A historical PP Required Date is valid as planning information, but ERPNext
	# does not permit a new Material Request whose required-by date is in the past.
	required_date = pp.get("custom_req_date") or today()
	mr.schedule_date = required_date if getdate(required_date) >= getdate(today()) else today()
	mr.set_warehouse = pp.get("for_warehouse") or ""
	for row in calculated:
		qty = requested_by_item.get(row["item_code"])
		if qty <= 0:
			frappe.throw("Request Qty for {0} must be greater than zero.".format(row["item_code"]))
		standard = standard_rows.get(row["item_code"]) or {}
		mr.append("items", {
			"item_code": row["item_code"],
			"qty": qty,
			"uom": row["uom"] or None,
			"schedule_date": mr.schedule_date,
			"warehouse": standard.get("warehouse") or pp.get("for_warehouse") or "",
			"from_warehouse": standard.get("from_warehouse") or "",
			"production_plan": pp.name,
		})
	mr.insert()
	frappe.get_doc({
		"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Production Plan",
		"reference_name": pp.name,
		"content": "Base Material Request created: <a href='/app/material-request/{0}'>{0}</a>".format(mr.name),
	}).insert(ignore_permissions=True)
	return {"material_request": mr.name}


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
	department = pp.get("custom_department")
	if not department and pp.get("custom_sales_order"):
		department = frappe.db.get_value("Sales Order", pp.custom_sales_order, "custom_department")
	if not department:
		frappe.throw(
			"Set a <b>Department</b> on the source Sales Order before creating the Purchase Request."
		)
	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Purchase"
	mr.company = company
	mr.custom_department = department
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
		# Pull the quotation reference for the plan. Artwork is approved in this workflow.
		_fetch_artwork_quotation(doc)
		notify_role("BOM Team", "Production Plan created: " + name, _msg(doc, "was created — please validate / create the BOM."), doc)
		notify_role("Supply Chain", "Production Plan created: " + name, _msg(doc, "was created — stock validation follows BOM validation."), doc)
	elif new_state == "BOM Validation":
		# CS Team released the job to planning (Draft -> BOM Validation): stamp the release time
		# once (keep the first release timestamp on any later re-entry).
		if not doc.get("custom_cs_released_on"):
			_stamp(name, {"custom_cs_released_on": now()})
		notify_role("BOM Team", "BOM validation needed: " + name, _msg(doc, "needs BOM validation — create/confirm BOMs (Open BOM Builder / Sync BOMs), then Confirm BOM."), doc)
	elif new_state == "Supply Chain Validation":
		# BOM validation completed → Supply Chain performs stock validation.
		_stamp(name, {"custom_bom_confirmed": 1, "custom_bom_by": _fullname(), "custom_bom_on": now()})
		notify_role("Supply Chain", "Stock validation needed: " + name, _msg(doc, "BOM confirmed — please validate stock."), doc)
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
	"""Fill a missing quotation reference through the linked Sales Order only."""
	try:
		if doc.get("custom_quote_no"):
			return
		source_type, source_name = _resolve_source(doc)
		if source_type == "Sales Order":
			quotation = _sales_order_quotation_ref(frappe.get_doc("Sales Order", source_name))
			if quotation:
				_stamp(doc.name, {"custom_quote_no": quotation})
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
