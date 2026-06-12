"""
BOM Builder API — serves the BOM Builder custom page.
Handles context loading, quantity recalculation, and ERPNext BOM creation.
"""
import json
import copy
import re
import frappe
from frappe.utils import flt, cint


# ─────────────────────────────────────────────────────────────
#  CONTEXT LOADER
# ─────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_bom_context(source_type, source_name):
	"""
	Return FG items for BOM building from a Sales Order or Savinda Quotation.
	When source is Sales Order, also accept a linked quotation name for better CB lookup.
	"""
	fg_items = []
	customer = ""

	if source_type == "Sales Order":
		so = frappe.get_doc("Sales Order", source_name)
		customer = so.customer or ""
		seen = set()
		for row in so.items:
			if not row.item_code or row.item_code in seen:
				continue
			seen.add(row.item_code)
			iname = frappe.db.get_value("Item", row.item_code, "item_name") or row.item_code
			cb    = _find_cb_for_fg(row.item_code)
			fg_items.append({
				"item_code":             row.item_code,
				"item_name":             iname,
				"qty":                   flt(row.qty),
				"calculation_breakdown": cb,
			})

	elif source_type == "Savinda Quotation":
		sq = frappe.get_doc("Savinda Quotation", source_name)
		customer = sq.customer_name or ""
		seen = {}
		for row in sq.items:
			if not row.finish_good or row.finish_good in seen:
				continue
			seen[row.finish_good] = True
			iname = frappe.db.get_value("Item", row.finish_good, "item_name") or row.finish_good
			fg_items.append({
				"item_code":             row.finish_good,
				"item_name":             iname,
				"qty":                   flt(row.qty),
				"calculation_breakdown": row.calculation_breakdown or "",
			})

	return {
		"fg_items":    fg_items,
		"customer":    customer,
		"source_type": source_type,
		"source_name": source_name,
	}


def _find_cb_for_fg(fg_item_code):
	"""
	Multi-strategy CB lookup for a FG item:
	S1: Savinda Quotation Item.finish_good
	S2: cost Item Calculation via Savinda Quotation Item.cost_item
	S3: cost Item Calculation via item_name pattern match
	"""
	# S1 — direct finish_good link
	r = frappe.db.sql("""
		SELECT calculation_breakdown
		FROM `tabSavinda Quotation Item`
		WHERE finish_good = %s AND IFNULL(calculation_breakdown,'') != ''
		ORDER BY modified DESC LIMIT 1
	""", fg_item_code)
	if r and r[0][0]: return r[0][0]

	# S2 — via cost_item on quotation row
	r = frappe.db.sql("""
		SELECT cic.calculation_breakdown
		FROM `tabSavinda Quotation Item` sqi
		JOIN `tabCost Item Calculation` cic ON cic.parent = sqi.cost_item
		WHERE sqi.finish_good = %s AND IFNULL(cic.calculation_breakdown,'') != ''
		ORDER BY sqi.modified DESC LIMIT 1
	""", fg_item_code)
	if r and r[0][0]: return r[0][0]

	# S3 — by item_name substring in cost Item
	item_name = frappe.db.get_value("Item", fg_item_code, "item_name") or ""
	if item_name:
		r = frappe.db.sql("""
			SELECT cic.calculation_breakdown
			FROM `tabCost Item Calculation` cic
			JOIN `tabcost Item` ci ON ci.name = cic.parent
			WHERE ci.cost_item_name LIKE %s AND IFNULL(cic.calculation_breakdown,'') != ''
			ORDER BY cic.modified DESC LIMIT 1
		""", ("%" + item_name[:30] + "%",))
		if r and r[0][0]: return r[0][0]

	return ""


@frappe.whitelist()
def search_cb_for_fg(fg_item_code, search_term=""):
	"""Return Calculation Breakdown options for manual selection."""
	item_name = frappe.db.get_value("Item", fg_item_code, "item_name") or fg_item_code
	kw = search_term or item_name[:20]

	r = frappe.db.sql("""
		SELECT DISTINCT sqi.calculation_breakdown, cb.customer_name, cb.item_qty
		FROM `tabSavinda Quotation Item` sqi
		JOIN `tabCalculation Breakdown` cb ON cb.name = sqi.calculation_breakdown
		WHERE sqi.finish_good = %s AND IFNULL(sqi.calculation_breakdown,'') != ''
		ORDER BY sqi.modified DESC LIMIT 20
	""", fg_item_code, as_dict=True)

	if not r:
		r = frappe.db.sql("""
			SELECT name AS calculation_breakdown, customer_name, item_qty
			FROM `tabCalculation Breakdown`
			WHERE customer_name LIKE %s OR ref LIKE %s
			ORDER BY modified DESC LIMIT 20
		""", ("%" + kw + "%", "%" + kw + "%"), as_dict=True)
	return r


# ─────────────────────────────────────────────────────────────
#  BOM DATA — operations + raw materials with SFG chain
# ─────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_bom_data(calculation_breakdown, mfg_qty, fg_item=""):
	"""
	Recalculate at mfg_qty and return:
	  • operations   — ALL selected specs with SFG chain pre-populated
	  • raw_materials — base material + other material rows at mfg_qty
	  • base_material — item code/name/qty for first operation input
	"""
	from nxtgen_savinda_pricing_calculator.api.offset_calculator import calculate

	mfg_qty = flt(mfg_qty) or 1
	cb      = frappe.get_doc("Calculation Breakdown", calculation_breakdown)

	state = {}
	try:
		state = json.loads(cb.ui_state or "{}")
	except Exception:
		pass

	form           = state.get("form", {}) or {}
	selected_specs = state.get("selected_specs", []) or []
	pricing_type   = (form.get("pricing_type") or cb.pricing_type or "Offset").strip()

	# ── Recalculate at mfg_qty ────────────────────────────────
	payload = copy.deepcopy(state)
	if payload.get("form"):
		payload["form"]["item_qty"] = mfg_qty
		payload["form"].pop("breakdown_qtys", None)

	try:
		calc_result = calculate(json.dumps(payload))
	except Exception as e:
		frappe.log_error(title="BOM Builder calc error", message=str(e))
		calc_result = {}

	sheet      = calc_result.get("sheet", {}) or {}
	cost_rows  = calc_result.get("cost_rows", []) or []

	# ── Base material ─────────────────────────────────────────
	base_mat       = form.get("base_material", "")
	base_mat_name  = frappe.db.get_value("Item", base_mat, "item_name") or base_mat if base_mat else ""
	base_mat_uom   = frappe.db.get_value("Item", base_mat, "stock_uom") or "Nos" if base_mat else "Nos"

	if pricing_type == "Flexo":
		base_input_qty = flt(sheet.get("reel_area") or 0)
		base_input_uom = "M2"
	else:
		base_input_qty = flt(sheet.get("full_sheet_qty") or 0)
		base_input_uom = base_mat_uom

	# After printing: req_cut_sheets includes wastage — this is what physically comes off the press
	# For Flexo: stickers_per_reel; for Offset: req_cut_sheets (cut_sheet_qty + wastage)
	after_print_qty = flt(
		sheet.get("req_cut_sheets") or
		sheet.get("cut_sheet_qty") or
		sheet.get("stickers_per_reel") or
		mfg_qty
	)
	after_print_uom = base_mat_uom if pricing_type == "Offset" else "Nos"

	# ── Sanitize for SFG naming ───────────────────────────────
	def _sfg_code(fg, spec):
		s = re.sub(r"[^A-Za-z0-9]+", "-", spec).strip("-")[:20].upper()
		return f"{fg}-{s}-SFG" if fg else s + "-SFG"

	def _sfg_name(fg, fg_iname, spec):
		return f"{fg_iname or fg} — {spec}"

	# ── Build operations (ALL selected specs) ─────────────────
	operations   = []
	print_found  = False
	fg_iname     = frappe.db.get_value("Item", fg_item, "item_name") or fg_item

	for spec in selected_specs:
		spec_name = spec.get("spec_name", "")
		ma        = spec.get("machine_assignment") or {}
		machine   = ma.get("machine", "")

		# ── Fetch manufacturing settings from Offset Spec master ──────────
		spec_bom_include  = 1
		spec_bom_operation = ""
		spec_bom_qi       = ""
		if spec_name:
			try:
				sd = frappe.get_cached_doc("Offset Spec", spec_name)
				spec_bom_include   = cint(getattr(sd, "bom_include", 1))
				spec_bom_operation = (getattr(sd, "bom_operation", "") or "").strip()
				spec_bom_qi        = (getattr(sd, "bom_qi_template", "") or "").strip()
			except Exception:
				pass

		# Workstation from machine
		workstation = ""
		if machine:
			workstation = frappe.db.get_value("Offset Machine", machine, "workstation") or ""
		# If spec has an ERPNext Operation set, try to fill workstation from it
		if spec_bom_operation and not workstation:
			workstation = frappe.db.get_value("Operation", spec_bom_operation, "workstation") or ""

		# Time estimate from calc row attribute_values
		time_mins = 60.0
		for row in cost_rows:
			if row.get("spec_name") == spec_name:
				av = row.get("attribute_values") or {}
				if av.get("total_hrs"):
					time_mins = round(flt(av["total_hrs"]) * 60, 2)
					break

		# Input qty — first "printing" spec gets base material; others get flow qty
		is_print = "print" in spec_name.lower()
		if is_print and not print_found:
			print_found   = True
			input_qty     = base_input_qty       # full_sheet_qty (or reel_area)
			input_uom     = base_input_uom       # board/material UOM
			output_qty    = after_print_qty      # req_cut_sheets (includes wastage)
			output_uom    = after_print_uom      # same as base material UOM
		else:
			input_qty  = after_print_qty
			input_uom  = after_print_uom
			output_qty = after_print_qty
			output_uom = after_print_uom

		sfg_code = _sfg_code(fg_item, spec_name)
		sfg_name = _sfg_name(fg_item, fg_iname, spec_name)

		# ── Materials for this operation ─────────────────────────────────
		op_materials = []
		no_of_colors = cint(form.get("no_of_colors", 0))

		if is_print:
			# Base material is shown in the Input column — NOT repeated in materials list
			# (removing duplication as per user feedback)

			# 2. Inks from machine_assignment (actual items selected in calculator)
			ma_inks = ma.get("inks", [])
			if ma_inks:
				for ci, ink_row in enumerate(ma_inks):
					ink_name = (ink_row.get("ink_name") or "").strip()
					ink_pct  = flt(ink_row.get("percentage", 100))
					if not ink_name:
						continue
					# Get linked ERPNext Item from Offset Ink master (may be empty)
					ink_erp_item = (frappe.db.get_value("Offset Ink", ink_name, "ink_item") or "").strip()
					# Qty from CB cost row for this ink
					ink_qty = 0
					for cr in cost_rows:
						if (cr.get("spec_name") == spec_name
								and (cr.get("cost_group") or "").lower() == "material"
								and cr.get("selected_item") != base_mat
								and (cr.get("selected_item_name") == ink_name
								     or cr.get("selected_item") == ink_erp_item)):
							ink_qty = flt(cr.get("req_qty", 0))
							break
					# Fallback: calculate from consumption_per_sqinch (Offset)
					if ink_qty == 0 and pricing_type == "Offset":
						consumption = flt(frappe.db.get_value("Offset Ink", ink_name, "consumption_per_sqinch") or 0)
						if consumption:
							cs_l   = flt(form.get("cut_sheet_l", 0))
							cs_w   = flt(form.get("cut_sheetw", 0)) or flt(form.get("cut_sheet_w", 0))
							cs_qty = flt(sheet.get("cut_sheet_qty", 0))
							ink_qty = cs_l * cs_w * cs_qty * consumption * (ink_pct / 100)
					# Fallback: consumption_per_sqm (Flexo)
					if ink_qty == 0 and pricing_type == "Flexo":
						consumption = flt(frappe.db.get_value("Offset Ink", ink_name, "consumption_per_sqm") or 0)
						if consumption:
							ink_qty = flt(sheet.get("reel_area", 0)) * consumption * (ink_pct / 100)
					op_materials.append({
						"item_code": ink_erp_item,
						"item_name": ink_name,
						"qty":       round(ink_qty, 8),
						"uom":       "KG",
						"cost_fact": f"Ink - {ink_name}",
						"include":   True,
						"label":     f"🎨 Color {ci+1}: {ink_name} ({ink_pct}%)",
					})
			elif no_of_colors > 0:
				# No inks assigned → generate N empty color slots
				for ci in range(no_of_colors):
					op_materials.append({
						"item_code": "",
						"item_name": "",
						"qty":       0,
						"uom":       "KG",
						"cost_fact": "Ink",
						"include":   True,
						"label":     f"🎨 Color {ci+1}",
					})

		else:
			# Non-print: materials come from the spec's cost_fact items/selected items in the CB
			for cf_row in spec.get("cost_facts", []):
				cf_name  = cf_row.get("cost_fact", "")
				sel_item = cf_row.get("selected_item", "")
				if not cf_name:
					continue
				# Only Material-group cost facts contribute physical raw materials
				cf_grp = frappe.db.get_value("Cost Fact", cf_name, "cost_group") or ""
				if cf_grp.lower() not in ("material", ""):
					continue
				# Qty from CB cost rows
				cf_qty = 0
				cf_uom = "Nos"
				for cr in cost_rows:
					if cr.get("spec_name") == spec_name and cr.get("cost_fact") == cf_name:
						cf_qty = flt(cr.get("req_qty", 0))
						cf_uom = cr.get("uom") or "Nos"
						break
				if sel_item:
					op_materials.append({
						"item_code": sel_item,
						"item_name": frappe.db.get_value("Item", sel_item, "item_name") or sel_item,
						"qty":       round(cf_qty, 6),
						"uom":       frappe.db.get_value("Item", sel_item, "stock_uom") or cf_uom,
						"cost_fact": cf_name,
						"include":   True,
						"label":     cf_name,
					})
				else:
					# No item selected; show available items from cost fact as options
					cf_items = frappe.get_all("Cost Fact Item",
						filters={"parent": cf_name}, fields=["item", "rate"])
					for cfi in cf_items:
						if not cfi.item: continue
						op_materials.append({
							"item_code": cfi.item,
							"item_name": frappe.db.get_value("Item", cfi.item, "item_name") or cfi.item,
							"qty":       round(cf_qty, 6),
							"uom":       frappe.db.get_value("Item", cfi.item, "stock_uom") or cf_uom,
							"cost_fact": cf_name,
							"include":   False,  # not auto-selected; user picks
							"label":     cf_name,
						})

			# Non-print ops: also include inks from Production Assignment (e.g. UV/WB Varnish)
			if not is_print:
				np_inks = ma.get("inks", [])
				for ci, ink_row in enumerate(np_inks):
					ink_name = (ink_row.get("ink_name") or "").strip()
					ink_pct  = flt(ink_row.get("percentage", 100))
					if not ink_name:
						continue
					ink_erp_item = (frappe.db.get_value("Offset Ink", ink_name, "ink_item") or "").strip()
					ink_qty = 0
					for cr in cost_rows:
						if (cr.get("spec_name") == spec_name
								and (cr.get("cost_group") or "").lower() == "material"
								and (cr.get("selected_item_name") == ink_name
								     or cr.get("selected_item") == ink_erp_item)):
							ink_qty = flt(cr.get("req_qty", 0))
							break
					if ink_qty == 0 and pricing_type == "Offset":
						consumption = flt(frappe.db.get_value("Offset Ink", ink_name, "consumption_per_sqinch") or 0)
						if consumption:
							cs_l   = flt(form.get("cut_sheet_l", 0))
							cs_w   = flt(form.get("cut_sheetw", 0)) or flt(form.get("cut_sheet_w", 0))
							cs_qty = flt(sheet.get("cut_sheet_qty", 0))
							ink_qty = cs_l * cs_w * cs_qty * consumption * (ink_pct / 100)
					if ink_qty == 0 and pricing_type == "Flexo":
						consumption = flt(frappe.db.get_value("Offset Ink", ink_name, "consumption_per_sqm") or 0)
						if consumption:
							ink_qty = flt(sheet.get("reel_area", 0)) * consumption * (ink_pct / 100)
					op_materials.append({
						"item_code": ink_erp_item,
						"item_name": ink_name,
						"qty":       round(ink_qty, 8),
						"uom":       "KG",
						"cost_fact": f"Ink - {ink_name}",
						"include":   True,
						"label":     f"🎨 {ink_name} ({ink_pct}%)",
					})

		sfg_code = _sfg_code(fg_item, spec_name)
		sfg_name = _sfg_name(fg_item, fg_iname, spec_name)

		operations.append({
			"spec_name":                   spec_name,
			"machine":                     machine,
			"workstation":                 workstation,
			"time_in_mins":                time_mins,
			"input_item_code":             "",
			"input_item_name":             "",
			"input_qty":                   round(input_qty, 4),
			"input_uom":                   input_uom,
			"sfg_code":                    sfg_code,
			"sfg_name":                    sfg_name,
			"output_qty":                  round(output_qty, 4),
			"output_uom":                  output_uom,
			"materials":                   op_materials,
			"is_print":                    is_print,
			"no_of_colors":                no_of_colors if is_print else 0,
			"split_to_item_unit":          False,
			# Manufacturing defaults from Offset Spec — can be overridden in BOM Builder
			"exclude_from_bom":            not bool(spec_bom_include),
			"erp_operation":               spec_bom_operation,
			"has_quality_inspection":      bool(spec_bom_qi),
			"quality_inspection_template": spec_bom_qi,
		})

	# ── Input chain is built by JS syncChain() AFTER sorting ─────
	# We only set base_material on the PRINT op here (it's the correct full_sheet_qty).
	# All other chaining (item_code, input_qty) is done client-side after sort.
	for op in operations:
		if op.get("is_print"):
			op["input_item_code"] = base_mat
			op["input_item_name"] = base_mat_name
			# input_qty already = full_sheet_qty (set during build)

	return {
		"operations":         operations,
		"raw_materials":      [],
		"sheet":              sheet,
		"mfg_qty":            mfg_qty,
		"item_qty":           mfg_qty,    # canonical order quantity (used at split point)
		"after_print_qty":    round(after_print_qty, 4),
		"base_material":      base_mat,
		"base_material_name": base_mat_name,
		"base_material_qty":  round(base_input_qty, 4),
		"base_material_uom":  base_input_uom,
		"pricing_type":       pricing_type,
	}


# ─────────────────────────────────────────────────────────────
#  BOM CREATION — multi-level SFG chain
# ─────────────────────────────────────────────────────────────

@frappe.whitelist()
def create_bom_chain(fg_item, mfg_qty, operations, extra_materials, is_default=0):
	"""
	Create the full SFG chain + FG BOM:
	1. Create ERPNext Items for each SFG (if not existing)
	2. Create sub-assembly BOM for each SFG
	3. Create FG BOM (raw material = last SFG, + FG-level ops)
	Returns list of all created/existing BOMs.
	"""
	if isinstance(operations, str):     operations     = json.loads(operations)
	if isinstance(extra_materials, str): extra_materials = json.loads(extra_materials)

	mfg_qty    = flt(mfg_qty) or 1
	is_default = cint(is_default)
	fg_uom     = frappe.db.get_value("Item", fg_item, "stock_uom") or "Nos"

	if not operations:
		frappe.throw("No operations defined. Please add at least one operation.")

	created_boms = []

	# ── Build active_ops: exclude flagged ops and re-link chain ──
	# Collect excluded SFG codes so downstream inputs can be re-linked
	excluded_sfg_codes = {
		(op.get("sfg_code") or "").strip()
		for op in operations
		if op.get("exclude_from_bom") and op.get("sfg_code")
	}
	# Original base material = input of first op (set by JS syncChain after sort)
	orig_first_input_code = (operations[0].get("input_item_code") or "").strip() if operations else ""
	orig_first_input_name = (operations[0].get("input_item_name") or "").strip() if operations else ""

	active_ops = []
	last_sfg_code = None
	last_sfg_name = None
	for op in operations:
		if op.get("exclude_from_bom"):
			continue
		op_copy = dict(op)
		# Re-link input if it pointed at an excluded op's SFG
		if op_copy.get("input_item_code") in excluded_sfg_codes:
			if last_sfg_code:
				op_copy["input_item_code"] = last_sfg_code
				op_copy["input_item_name"] = last_sfg_name or last_sfg_code
			else:
				# First active op: trace back to original base material
				op_copy["input_item_code"] = orig_first_input_code
				op_copy["input_item_name"] = orig_first_input_name
		active_ops.append(op_copy)
		last_sfg_code = op_copy.get("sfg_code")
		last_sfg_name = op_copy.get("sfg_name")

	if not active_ops:
		frappe.throw("All operations are excluded from BOM. Include at least one operation.")

	# ── Step 1: ensure SFG items exist (active ops only) ─────
	for op in active_ops:
		sfg_code = (op.get("sfg_code") or "").strip()
		sfg_name = (op.get("sfg_name") or sfg_code).strip()
		if not sfg_code:
			continue
		if not frappe.db.exists("Item", sfg_code):
			_create_sfg_item(sfg_code, sfg_name, fg_item=fg_item)
		# Set Quality Inspection Template on the SFG Item if required
		if op.get("has_quality_inspection") and op.get("quality_inspection_template"):
			try:
				frappe.db.set_value("Item", sfg_code, "quality_inspection_template",
				                    op["quality_inspection_template"])
			except Exception:
				pass

	# ── Step 2: create sub-assembly BOM for each SFG ─────────
	# Track previous op's QI template so we can set it on the input row of the consuming BOM
	prev_qi_template = None

	for i, op in enumerate(active_ops):
		sfg_code = (op.get("sfg_code") or "").strip()
		if not sfg_code:
			prev_qi_template = op.get("quality_inspection_template") if op.get("has_quality_inspection") else None
			continue

		sfg_uom = frappe.db.get_value("Item", sfg_code, "stock_uom") or "Nos"
		op_qty  = flt(op.get("output_qty") or mfg_qty)

		# Check if BOM already exists and is active
		existing_row = frappe.db.get_value(
			"BOM", {"item": sfg_code, "docstatus": 1, "is_active": 1},
			["name", "quantity"], as_dict=True
		)
		if existing_row:
			# Reuse if qty matches; otherwise deactivate old and recreate
			if abs(flt(existing_row.quantity) - op_qty) < 0.01:
				created_boms.append({"bom_name": existing_row.name, "item": sfg_code, "reused": True})
				prev_qi_template = op.get("quality_inspection_template") if op.get("has_quality_inspection") else None
				continue
			else:
				# Qty changed — deactivate old BOM so we can create updated one
				frappe.db.set_value("BOM", existing_row.name, "is_active", 0)
				created_boms.append({"bom_name": existing_row.name, "item": sfg_code,
				                     "reused": False, "deactivated": True})
				frappe.db.commit()

		sfg_bom = frappe.get_doc({
			"doctype":         "BOM",
			"item":            sfg_code,
			"quantity":        op_qty,
			"uom":             sfg_uom,
			"is_default":      0,
			"is_active":       1,
			"with_operations": 1 if op.get("workstation") else 0,
			"rm_cost_as_per":  "Valuation Rate",
		})

		# Raw material = input item (base material or previous SFG)
		input_code = (op.get("input_item_code") or "").strip()
		input_qty  = flt(op.get("input_qty") or op_qty)
		input_uom  = op.get("input_uom") or sfg_uom

		if input_code:
			item_uom = frappe.db.get_value("Item", input_code, "stock_uom") or input_uom
			input_row = {
				"item_code": input_code,
				"item_name": op.get("input_item_name") or input_code,
				"qty":       input_qty,
				"uom":       item_uom,
				"stock_uom": item_uom,
			}
			# If the PREVIOUS operation had QI, set its template on this input row
			# (QI is inspected at the point of consumption — standard ERPNext pattern)
			if prev_qi_template:
				try:
					input_row["quality_inspection_template"] = prev_qi_template
				except Exception:
					pass
			sfg_bom.append("items", input_row)

		# Add per-operation materials (inks, foils, lamination material, etc.)
		op_mats = op.get("materials") or []
		if not op_mats and i == 0:
			op_mats = extra_materials or []  # fallback to global list for first op
		for mat in op_mats:
			if not mat.get("include", True):
				continue
			ic = mat.get("item_code", "")
			if not ic or ic == input_code:
				continue
			mat_uom = mat.get("uom") or frappe.db.get_value("Item", ic, "stock_uom") or "Nos"
			sfg_bom.append("items", {
				"item_code": ic,
				"item_name": mat.get("item_name", ic),
				"qty":       flt(mat.get("qty", 0)),
				"uom":       mat_uom,
				"stock_uom": mat_uom,
				"rate":      flt(mat.get("rate", 0)),
			})

		# Operation — use explicitly selected ERPNext Operation, or auto-create from spec name
		if op.get("workstation"):
			op_name_key = (op.get("erp_operation") or "").strip() or op.get("spec_name", sfg_code)
			op_name = _get_or_create_operation(op_name_key)
			sfg_bom.append("operations", {
				"operation":    op_name,
				"workstation":  op["workstation"],
				"time_in_mins": flt(op.get("time_in_mins", 60)),
				"description":  f"{op.get('machine','')} — {op.get('spec_name','')}".strip(" —"),
			})

		sfg_bom.insert(ignore_permissions=True)
		sfg_bom.submit()
		frappe.db.commit()
		created_boms.append({"bom_name": sfg_bom.name, "item": sfg_code, "reused": False})
		# Pass this op's QI template to the next op's input row
		prev_qi_template = op.get("quality_inspection_template") if op.get("has_quality_inspection") else None

	# ── Step 3: FG BOM — raw material = last ACTIVE SFG ─────
	existing_fg = frappe.db.get_value("BOM", {"item": fg_item, "docstatus": 1, "is_active": 1}, "name")
	if existing_fg:
		frappe.throw(
			f"Active BOM <b>{existing_fg}</b> already exists for {fg_item}. Deactivate it first.",
			frappe.ValidationError,
		)

	last_op  = active_ops[-1]
	last_sfg = (last_op.get("sfg_code") or "").strip()
	last_sfg_uom = frappe.db.get_value("Item", last_sfg, "stock_uom") or "Nos" if last_sfg else fg_uom

	fg_bom = frappe.get_doc({
		"doctype":         "BOM",
		"item":            fg_item,
		"quantity":        mfg_qty,
		"uom":             fg_uom,
		"is_default":      is_default,
		"is_active":       1,
		"with_operations": 0,
		"rm_cost_as_per":  "Valuation Rate",
	})

	if last_sfg:
		fg_item_row = {
			"item_code": last_sfg,
			"item_name": last_op.get("sfg_name") or last_sfg,
			"qty":       flt(last_op.get("output_qty") or mfg_qty),
			"uom":       last_sfg_uom,
			"stock_uom": last_sfg_uom,
		}
		# If the last operation had QI, set it on the FG BOM's SFG input row
		if prev_qi_template:
			try:
				fg_item_row["quality_inspection_template"] = prev_qi_template
			except Exception:
				pass
		fg_bom.append("items", fg_item_row)

	fg_bom.insert(ignore_permissions=True)
	fg_bom.submit()
	frappe.db.commit()
	created_boms.append({"bom_name": fg_bom.name, "item": fg_item, "reused": False})

	return {"created_boms": created_boms, "fg_bom": fg_bom.name}


def _create_sfg_item(item_code, item_name, fg_item=""):
	"""
	Create a Semi-Finished Good ERPNext Item.
	Inherits Department and Item Group abbreviations from the FG item
	(or falls back to system defaults) to satisfy mandatory custom fields.
	"""
	# ── Resolve item group ───────────────────────────────────
	item_group = "Semi Finished Goods"
	if not frappe.db.exists("Item Group", item_group):
		item_group = frappe.db.get_value("Item", fg_item, "item_group") or "All Item Groups"

	ig_abbr = frappe.db.get_value("Item Group", item_group, "custom_abbr") or ""

	# ── Resolve department from FG item → system default ────
	dept = ""
	dept_abbr = ""
	if fg_item:
		dept = frappe.db.get_value("Item", fg_item, "custom_department") or ""
	if not dept:
		company = frappe.defaults.get_global_default("company") or ""
		if company:
			dept = frappe.db.get_value("Department", {"company": company, "is_group": 0}, "name") or ""
		if not dept:
			dept = frappe.db.get_value("Department", {"is_group": 0}, "name") or ""
	if dept:
		dept_abbr = frappe.db.get_value("Department", dept, "custom_abbr") or ""

	fields = {
		"doctype":                "Item",
		"item_code":              item_code,
		"item_name":              item_name,
		"description":            item_name,
		"item_group":             item_group,
		"stock_uom":              "Nos",
		"is_stock_item":          1,
		"is_sales_item":          0,
		"is_purchase_item":       0,
	}
	if dept:
		fields["custom_department"]      = dept
		fields["custom_department_abbr"] = dept_abbr
	if ig_abbr:
		fields["custom_item_group_abbr"] = ig_abbr

	doc = frappe.get_doc(fields)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()


def _get_or_create_operation(op_name):
	"""Get or create an ERPNext Operation record."""
	if frappe.db.exists("Operation", op_name):
		return op_name
	try:
		frappe.get_doc({"doctype": "Operation", "name": op_name}).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		pass
	return op_name


# ─────────────────────────────────────────────────────────────
#  BOM BUILDER CONFIG — save / load per FG
# ─────────────────────────────────────────────────────────────

@frappe.whitelist()
def save_bom_config(fg_item, operations, pricing_type="Offset"):
	"""Persist the BOM Builder operations chain for a given FG item."""
	config = {
		"doctype":      "BOM Builder Config",
		"fg_item":      fg_item,
		"pricing_type": pricing_type,
		"config_json":  operations if isinstance(operations, str) else json.dumps(operations),
	}
	if frappe.db.exists("BOM Builder Config", fg_item):
		doc = frappe.get_doc("BOM Builder Config", fg_item)
		doc.pricing_type = pricing_type
		doc.config_json  = config["config_json"]
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc(config).insert(ignore_permissions=True)
	frappe.db.commit()
	return {"saved": True}


@frappe.whitelist()
def load_bom_config(fg_item):
	"""Load saved BOM Builder operations chain for a given FG item."""
	if not frappe.db.exists("BOM Builder Config", fg_item):
		return None
	doc = frappe.get_doc("BOM Builder Config", fg_item)
	ops = []
	try:
		ops = json.loads(doc.config_json or "[]")
	except Exception:
		pass
	return {"operations": ops, "pricing_type": doc.pricing_type or "Offset"}


@frappe.whitelist()
def check_existing_bom(fg_item):
	"""Return the active BOM for this FG if one exists."""
	bom = frappe.db.get_value(
		"BOM",
		{"item": fg_item, "docstatus": 1, "is_active": 1},
		["name", "item", "quantity"],
		as_dict=True,
	)
	return bom or None


@frappe.whitelist()
def disable_bom(bom_name):
	"""Deactivate an existing BOM so a new one can be created."""
	frappe.db.set_value("BOM", bom_name, "is_active", 0)
	frappe.db.commit()
	return {"disabled": bom_name}


@frappe.whitelist()
def get_erpnext_operations(search_term=""):
	"""Return ERPNext Operation list for the BOM Builder operation picker."""
	filters = {}
	if search_term:
		filters["name"] = ["like", f"%{search_term}%"]
	return frappe.get_all(
		"Operation",
		filters=filters,
		fields=["name", "workstation"],
		order_by="name asc",
		limit=50,
	)


# keep old create_bom for backward compat (single-level BOM)
@frappe.whitelist()
def create_bom(fg_item, mfg_qty, operations, raw_materials, is_default=0):
	"""Legacy single-level BOM creation (flat, no SFG chain)."""
	if isinstance(operations, str):    operations    = json.loads(operations)
	if isinstance(raw_materials, str): raw_materials = json.loads(raw_materials)
	mfg_qty = flt(mfg_qty) or 1
	fg_uom  = frappe.db.get_value("Item", fg_item, "stock_uom") or "Nos"

	existing = frappe.db.get_value("BOM", {"item": fg_item, "docstatus": 1, "is_active": 1}, "name")
	if existing:
		frappe.throw(f"Active BOM {existing} exists for {fg_item}. Deactivate it first.")

	bom = frappe.get_doc({
		"doctype": "BOM", "item": fg_item, "quantity": mfg_qty, "uom": fg_uom,
		"is_default": cint(is_default), "is_active": 1,
		"with_operations": 1 if operations else 0, "rm_cost_as_per": "Valuation Rate",
	})
	for mat in raw_materials:
		ic = mat.get("item_code", "")
		if not ic or flt(mat.get("qty", 0)) <= 0: continue
		iu = mat.get("uom") or frappe.db.get_value("Item", ic, "stock_uom") or "Nos"
		bom.append("items", {"item_code": ic, "item_name": mat.get("item_name", ic),
		                      "qty": flt(mat["qty"]), "uom": iu, "stock_uom": iu,
		                      "rate": flt(mat.get("rate", 0))})
	for i, op in enumerate(operations):
		if not op.get("workstation"): continue
		bom.append("operations", {"operation": _get_or_create_operation(op.get("spec_name", f"Op {i+1}")),
		                           "workstation": op["workstation"],
		                           "time_in_mins": flt(op.get("time_in_mins", 60))})
	bom.insert(ignore_permissions=True)
	bom.submit()
	frappe.db.commit()
	return {"bom_name": bom.name, "item": fg_item}
