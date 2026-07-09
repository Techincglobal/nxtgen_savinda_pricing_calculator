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
				"cost_item":             frappe.db.get_value("Item", row.item_code, "custom_cost_item") or "",
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
				"cost_item":             (frappe.db.get_value("Item", row.finish_good, "custom_cost_item")
				                          or getattr(row, "cost_item", "") or ""),
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
	S0: FG Item's own Cost Item link (custom_cost_item) → its calculation breakdown
	S1: Savinda Quotation Item.finish_good
	S2: cost Item Calculation via Savinda Quotation Item.cost_item
	S3: cost Item Calculation via item_name pattern match
	"""
	# S0 — the FG's linked Cost Item (set at FG creation) is the authoritative source
	if frappe.db.has_column("Item", "custom_cost_item"):
		cost_item = frappe.db.get_value("Item", fg_item_code, "custom_cost_item")
		if cost_item:
			cb = frappe.db.get_value(
				"Cost Item Calculation",
				{"parent": cost_item, "calculation_breakdown": ["!=", ""]},
				"calculation_breakdown", order_by="idx asc",
			)
			if cb:
				return cb

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
	mat_type = (form.get("material_type") or "Existing").strip()
	if mat_type == "Custom":
		base_mat      = ""
		base_mat_name = (form.get("custom_material_name") or "").strip()
		base_mat_uom  = "Nos"
	else:
		base_mat      = form.get("base_material", "")
		base_mat_name = frappe.db.get_value("Item", base_mat, "item_name") or base_mat if base_mat else ""
		base_mat_uom  = frappe.db.get_value("Item", base_mat, "stock_uom") or "Nos" if base_mat else "Nos"

	# BOM uses NET quantities only (no wastage). Wastage is added later at the
	# production-planning stage — so we use Cut Sheet Qty, NOT Req Cut Sheets.
	no_cuts       = max(cint(form.get("no_of_cuts")) or 1, 1)
	cut_sheet_qty = flt(sheet.get("cut_sheet_qty") or 0)

	if pricing_type == "Flexo":
		base_input_qty  = flt(sheet.get("reel_area_net") or sheet.get("reel_area") or 0)
		base_input_uom  = "M2"
		after_print_qty = flt(sheet.get("stickers_per_reel") or mfg_qty)
		after_print_uom = "Nos"
	else:
		# NET full sheets (no wastage) = net cut sheets ÷ cuts-per-sheet
		base_input_qty  = (cut_sheet_qty / no_cuts) if no_cuts else cut_sheet_qty
		base_input_uom  = base_mat_uom
		# SFG flow qty = NET cut sheet qty (not req_cut_sheets, which includes wastage)
		after_print_qty = cut_sheet_qty or mfg_qty
		after_print_uom = base_mat_uom

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

		# Time estimate + machine hourly rate from the costing breakdown rows
		time_mins = 60.0
		hour_rate = 0.0
		for row in cost_rows:
			if row.get("spec_name") != spec_name:
				continue
			av = row.get("attribute_values") or {}
			if av.get("total_hrs") and time_mins == 60.0:
				time_mins = round(flt(av["total_hrs"]) * 60, 2)
			# machine rate = the per-hour rate used for this machine in the CB
			if not hour_rate and flt(row.get("rate")) and (row.get("selected_item") == machine or av.get("machine")):
				hour_rate = flt(row.get("rate"))
		# fall back to the machine master; else 0
		if not hour_rate and machine:
			hour_rate = flt(frappe.db.get_value("Offset Machine", machine, "machine_cost_per_hour")) or 0.0

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
			"hour_rate":                   hour_rate,
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
			"output_is_fg":                False,
			# Printing operations are ALWAYS included by default; others follow the
			# Offset Spec's bom_include flag. Either way it's editable in the builder.
			"exclude_from_bom":            False if is_print else (not bool(spec_bom_include)),
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

	_validate_materials(operations)

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

	# ── FG-producer op (Output = FG). Usually the last step (sorting). ──
	# The chain ends there — anything after it is dropped, and NO separate FG BOM
	# is created (this op's BOM produces the FG directly).
	fg_idx = next((i for i, op in enumerate(active_ops) if op.get("output_is_fg")), None)
	if fg_idx is not None:
		active_ops = active_ops[:fg_idx + 1]

	# Guard: refuse if an active FG BOM already exists (deactivate it first)
	existing_fg = frappe.db.get_value("BOM", {"item": fg_item, "docstatus": 1, "is_active": 1}, "name")
	if existing_fg:
		frappe.throw(
			f"Active BOM <b>{existing_fg}</b> already exists for {fg_item}. Deactivate it first.",
			frappe.ValidationError,
		)

	# ── Step 1: ensure SFG items exist (skip the FG-producer op — it makes the FG) ─
	for idx, op in enumerate(active_ops):
		if fg_idx is not None and idx == fg_idx:
			continue
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

	# ── Step 2: create a BOM per operation ───────────────────
	# Every BOM is built at qty 1 (per-unit actual consumption — NO wastage;
	# wastage is handled later at production-planning level). Input + material
	# quantities are normalised to "per 1 output" using each op's output_qty.
	# The FG-producer op (fg_idx) builds a BOM whose item IS the FG.
	prev_qi_template = None

	for i, op in enumerate(active_ops):
		is_fg_op    = (fg_idx is not None and i == fg_idx)
		target_item = fg_item if is_fg_op else (op.get("sfg_code") or "").strip()
		if not target_item:
			prev_qi_template = op.get("quality_inspection_template") if op.get("has_quality_inspection") else None
			continue

		target_uom = frappe.db.get_value("Item", target_item, "stock_uom") or "Nos"
		out_qty    = flt(op.get("output_qty")) or 1        # per-unit divisor

		# Reuse an existing qty-1 SFG BOM; recreate if it was built at another qty.
		# (FG already guarded above, so only SFGs get this check.)
		if not is_fg_op:
			existing_row = frappe.db.get_value(
				"BOM", {"item": target_item, "docstatus": 1, "is_active": 1},
				["name", "quantity"], as_dict=True
			)
			if existing_row:
				if abs(flt(existing_row.quantity) - 1) < 0.001:
					created_boms.append({"bom_name": existing_row.name, "item": target_item, "reused": True})
					prev_qi_template = op.get("quality_inspection_template") if op.get("has_quality_inspection") else None
					continue
				frappe.db.set_value("BOM", existing_row.name, "is_active", 0)
				created_boms.append({"bom_name": existing_row.name, "item": target_item,
				                     "reused": False, "deactivated": True})
				frappe.db.commit()

		bom = frappe.get_doc({
			"doctype":         "BOM",
			"item":            target_item,
			"quantity":        1,
			"uom":             target_uom,
			"is_default":      (is_default if is_fg_op else 0),
			"is_active":       1,
			"with_operations": 1 if ((op.get("erp_operation") or "").strip() or (op.get("spec_name") or "").strip()) else 0,
			"rm_cost_as_per":  "Valuation Rate",
		})

		# Raw material = input item (base material or previous SFG), per 1 output
		input_code = (op.get("input_item_code") or "").strip()
		per_input  = (flt(op.get("input_qty") or 0) / out_qty) if out_qty else flt(op.get("input_qty") or 0)
		input_uom  = op.get("input_uom") or target_uom
		if input_code:
			item_uom = frappe.db.get_value("Item", input_code, "stock_uom") or input_uom
			input_row = {
				"item_code": input_code,
				"item_name": op.get("input_item_name") or input_code,
				"qty":       per_input or 1,
				"uom":       item_uom,
				"stock_uom": item_uom,
			}
			if prev_qi_template:
				input_row["quality_inspection_template"] = prev_qi_template
			bom.append("items", input_row)

		# Per-operation materials (inks, foils, lamination film, etc.), per 1 output
		op_mats = op.get("materials") or []
		if not op_mats and i == 0:
			op_mats = extra_materials or []
		for mat in op_mats:
			if not mat.get("include", True):
				continue
			ic = mat.get("item_code", "")
			if not ic or ic == input_code:
				continue
			mat_uom = mat.get("uom") or frappe.db.get_value("Item", ic, "stock_uom") or "Nos"
			per_mat = (flt(mat.get("qty", 0)) / out_qty) if out_qty else flt(mat.get("qty", 0))
			bom.append("items", {
				"item_code": ic,
				"item_name": mat.get("item_name", ic),
				"qty":       per_mat,
				"uom":       mat_uom,
				"stock_uom": mat_uom,
				"rate":      flt(mat.get("rate", 0)),
			})

		# Operation — ERPNext requires a workstation; auto-create one from the machine
		# (or the operation name) when the machine has none.
		op_name_key = (op.get("erp_operation") or "").strip() or (op.get("spec_name") or "").strip()
		if op_name_key:
			op_name = _get_or_create_operation(op_name_key)
			_hr = flt(op.get("hour_rate") or 0)
			ws  = (op.get("workstation") or "").strip() or _get_or_create_workstation(
				(op.get("machine") or "").strip() or op_name_key, _hr)
			# Per-unit time: whole-run minutes ÷ output qty (BOM is qty 1)
			_per_time = (flt(op.get("time_in_mins") or 0) / out_qty) if out_qty else flt(op.get("time_in_mins") or 0)
			if ws:
				bom.append("operations", {
					"operation":      op_name,
					"workstation":    ws,
					"time_in_mins":   _per_time,
					"hour_rate":      _hr,
					"base_hour_rate": _hr,
					"description":    f"{op.get('machine','')} — {op.get('spec_name','')}".strip(" —"),
				})

		bom.insert(ignore_permissions=True)
		bom.submit()
		frappe.db.commit()
		created_boms.append({"bom_name": bom.name, "item": target_item, "reused": False, "is_fg": is_fg_op})
		prev_qi_template = op.get("quality_inspection_template") if op.get("has_quality_inspection") else None

	# ── Step 3: final FG BOM — ONLY if no op was marked "Output = FG" ─────
	if fg_idx is not None:
		fg_bom_name = next((b["bom_name"] for b in created_boms if b.get("is_fg")), None)
		return {"created_boms": created_boms, "fg_bom": fg_bom_name}

	last_op      = active_ops[-1]
	last_sfg     = (last_op.get("sfg_code") or "").strip()
	last_sfg_uom = (frappe.db.get_value("Item", last_sfg, "stock_uom") or "Nos") if last_sfg else fg_uom

	fg_bom = frappe.get_doc({
		"doctype":         "BOM",
		"item":            fg_item,
		"quantity":        1,
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
			"qty":       1,
			"uom":       last_sfg_uom,
			"stock_uom": last_sfg_uom,
		}
		if prev_qi_template:
			fg_item_row["quality_inspection_template"] = prev_qi_template
		fg_bom.append("items", fg_item_row)

	fg_bom.insert(ignore_permissions=True)
	fg_bom.submit()
	frappe.db.commit()
	created_boms.append({"bom_name": fg_bom.name, "item": fg_item, "reused": False, "is_fg": True})

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


def _validate_materials(operations):
	"""Refuse to build if any INCLUDED material row (on a non-excluded op) has no
	actual item selected — e.g. empty ink/colour slots. Warns the user to pick items."""
	missing = []
	for op in (operations or []):
		if op.get("exclude_from_bom"):
			continue
		for mat in (op.get("materials") or []):
			if mat.get("include", True) and not (mat.get("item_code") or "").strip():
				missing.append("<b>{}</b> → {}".format(
					op.get("spec_name") or "?",
					mat.get("label") or mat.get("cost_fact") or "material"))
	if missing:
		frappe.throw(
			"Select an actual item for these material rows before creating the BOM "
			"(or untick them):<br>• " + "<br>• ".join(missing),
			title="Material item not selected",
		)


def _get_or_create_workstation(ws_name, hour_rate=0):
	"""Get or create a Workstation. ERPNext requires a workstation on a BOM
	operation, so when a machine has none we auto-create one named after it,
	carrying the costing hour-rate."""
	ws_name = (ws_name or "").strip()
	if not ws_name:
		return ""
	if frappe.db.exists("Workstation", ws_name):
		return ws_name
	try:
		doc = frappe.get_doc({
			"doctype": "Workstation",
			"workstation_name": ws_name,
			"hour_rate": flt(hour_rate),
		})
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name
	except Exception:
		frappe.log_error(title="pricing_calculator: workstation create failed",
		                 message=frappe.get_traceback())
		return ""


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


# ─────────────────────────────────────────────────────────────
#  MULTI-FG BOM — common SFGs shared, per-FG tail (consolidated)
# ─────────────────────────────────────────────────────────────

def _bb_code(s):
	return re.sub(r"[^A-Za-z0-9]+", "-", (s or "")).strip("-").upper()[:20]


def _mk_qty1_bom(item_code, input_code, input_name, per_input_qty, materials, op,
                 prev_qi=None, is_default=0, allow_reuse=True):
	"""Create + submit one qty-1 BOM for item_code. Returns (bom_name, reused)."""
	if allow_reuse:
		ex = frappe.db.get_value("BOM", {"item": item_code, "docstatus": 1, "is_active": 1},
		                         ["name", "quantity"], as_dict=True)
		if ex:
			if abs(flt(ex.quantity) - 1) < 0.001:
				return ex.name, True
			frappe.db.set_value("BOM", ex.name, "is_active", 0)
			frappe.db.commit()

	uom = frappe.db.get_value("Item", item_code, "stock_uom") or "Nos"
	bom = frappe.get_doc({
		"doctype": "BOM", "item": item_code, "quantity": 1, "uom": uom,
		"is_default": cint(is_default), "is_active": 1,
		"with_operations": 1 if ((op.get("erp_operation") or "").strip() or (op.get("spec_name") or "").strip()) else 0,
		"rm_cost_as_per": "Valuation Rate",
	})
	if input_code:
		iu = frappe.db.get_value("Item", input_code, "stock_uom") or "Nos"
		row = {"item_code": input_code, "item_name": input_name or input_code,
		       "qty": per_input_qty or 1, "uom": iu, "stock_uom": iu}
		if prev_qi:
			row["quality_inspection_template"] = prev_qi
		bom.append("items", row)
	for mat in (materials or []):
		if not mat.get("include", True):
			continue
		ic = mat.get("item_code", "")
		if not ic or ic == input_code:
			continue
		mu = mat.get("uom") or frappe.db.get_value("Item", ic, "stock_uom") or "Nos"
		bom.append("items", {"item_code": ic, "item_name": mat.get("item_name", ic),
		                     "qty": flt(mat.get("qty", 0)), "uom": mu, "stock_uom": mu,
		                     "rate": flt(mat.get("rate", 0))})
	_op_key = (op.get("erp_operation") or "").strip() or (op.get("spec_name") or "").strip()
	if _op_key:
		opn = _get_or_create_operation(_op_key)
		_hr = flt(op.get("hour_rate") or 0)
		_ws = (op.get("workstation") or "").strip() or _get_or_create_workstation(
			(op.get("machine") or "").strip() or _op_key, _hr)
		_ot = flt(op.get("output_qty")) or 1
		_per_time = (flt(op.get("time_in_mins") or 0) / _ot) if _ot else flt(op.get("time_in_mins") or 0)
		if _ws:
			bom.append("operations", {"operation": opn, "workstation": _ws,
			          "time_in_mins": _per_time,
			          "hour_rate": _hr, "base_hour_rate": _hr,
			          "description": f"{op.get('machine','')} — {op.get('spec_name','')}".strip(" —")})
	bom.insert(ignore_permissions=True)
	bom.submit()
	frappe.db.commit()
	return bom.name, False


def _active_ops(operations):
	"""Exclude flagged ops and re-link the chain across the gaps."""
	excluded = {(op.get("sfg_code") or "").strip() for op in operations
	            if op.get("exclude_from_bom") and op.get("sfg_code")}
	first_in  = (operations[0].get("input_item_code") or "").strip() if operations else ""
	first_inn = (operations[0].get("input_item_name") or "").strip() if operations else ""
	out, last, lastn = [], None, None
	for op in operations:
		if op.get("exclude_from_bom"):
			continue
		c = dict(op)
		if c.get("input_item_code") in excluded:
			c["input_item_code"] = last or first_in
			c["input_item_name"] = lastn or first_inn
		out.append(c)
		last, lastn = c.get("sfg_code"), c.get("sfg_name")
	return out, first_in, first_inn


def _per_unit_mats(op):
	out_qty = flt(op.get("output_qty")) or 1
	res = []
	for m in (op.get("materials") or []):
		mm = dict(m)
		mm["qty"] = flt(m.get("qty", 0)) / out_qty if out_qty else flt(m.get("qty", 0))
		res.append(mm)
	return res


@frappe.whitelist()
def create_multi_bom(fg_items, cost_item, mfg_qty, operations, extra_materials, is_default=0):
	"""Build BOMs for several FGs of one family in one pass.

	Operations up to the 🧩 Final-Common-SFG marker build ONE shared SFG chain
	(codes based on the Cost Item). Operations after it build per-FG SFGs, ending
	at the 🏁 Output=FG op (or a final FG BOM). All BOMs are qty 1 (per-unit).
	A single FG falls back to the normal create_bom_chain.
	"""
	if isinstance(fg_items, str):      fg_items      = json.loads(fg_items or "[]")
	if isinstance(operations, str):    operations    = json.loads(operations or "[]")
	if isinstance(extra_materials, str): extra_materials = json.loads(extra_materials or "[]")
	fg_items = [f for f in (fg_items or []) if f]
	if not fg_items:
		frappe.throw("Select at least one FG item.")
	if len(fg_items) == 1:
		return create_bom_chain(fg_items[0], mfg_qty, json.dumps(operations),
		                        json.dumps(extra_materials), is_default)
	if not operations:
		frappe.throw("No operations defined.")

	_validate_materials(operations)

	active, first_in, first_inn = _active_ops(operations)
	if not active:
		frappe.throw("All operations are excluded from BOM.")

	fg_idx = next((i for i, op in enumerate(active) if op.get("output_is_fg")), None)
	cidx   = next((i for i, op in enumerate(active) if op.get("common_sfg_point")), None)
	if cidx is None:
		# default: everything before the FG-producer is common (all SFGs shared)
		cidx = (fg_idx - 1) if fg_idx is not None else (len(active) - 1)

	created, fg_boms = [], []
	subj = _bb_code(cost_item) or "COMMON"

	# ── Common SFG chain (built once) ──
	prev_qi = None
	last_code = last_name = None
	for i in range(0, cidx + 1):
		op = active[i]
		code = f"{subj}-{_bb_code(op.get('spec_name'))}-SFG"
		name = op.get("sfg_name") or code
		if not frappe.db.exists("Item", code):
			_create_sfg_item(code, name, fg_item=fg_items[0])
		out_qty = flt(op.get("output_qty")) or 1
		in_code = first_in if i == 0 else last_code
		in_name = first_inn if i == 0 else last_name
		per_in  = (flt(op.get("input_qty") or 0) / out_qty) if out_qty else flt(op.get("input_qty") or 0)
		mats    = _per_unit_mats(op) if i > 0 else (_per_unit_mats(op) or extra_materials)
		bn, reused = _mk_qty1_bom(code, in_code, in_name, per_in, mats, op, prev_qi)
		created.append({"bom_name": bn, "item": code, "reused": reused, "common": True})
		last_code, last_name = code, name
		prev_qi = op.get("quality_inspection_template") if op.get("has_quality_inspection") else None

	tail = active[cidx + 1:]

	# ── Per-FG tail ──
	for fg in fg_items:
		if frappe.db.get_value("BOM", {"item": fg, "docstatus": 1, "is_active": 1}, "name"):
			created.append({"item": fg, "skipped": "active BOM exists"})
			continue
		fg_uom = frappe.db.get_value("Item", fg, "stock_uom") or "Nos"

		if not tail:
			# FG consumes the last common SFG directly
			bn, _ = _mk_qty1_bom(fg, last_code, last_name, 1.0, [], {"spec_name": "Assembly"},
			                     prev_qi, is_default=is_default, allow_reuse=False)
			created.append({"bom_name": bn, "item": fg, "is_fg": True})
			fg_boms.append(bn)
			continue

		p_qi, p_code, p_name = prev_qi, last_code, last_name
		produced_fg = False
		for j, op in enumerate(tail):
			is_fg_op = bool(op.get("output_is_fg"))
			target = fg if is_fg_op else f"{_bb_code(fg)}-{_bb_code(op.get('spec_name'))}-SFG"
			if not is_fg_op and not frappe.db.exists("Item", target):
				_create_sfg_item(target, op.get("sfg_name") or target, fg_item=fg)
			out_qty = flt(op.get("output_qty")) or 1
			per_in  = (flt(op.get("input_qty") or 0) / out_qty) if out_qty else 1.0
			bn, _ = _mk_qty1_bom(target, p_code, p_name, per_in, _per_unit_mats(op), op,
			                     p_qi, is_default=(is_default if is_fg_op else 0),
			                     allow_reuse=(not is_fg_op))
			created.append({"bom_name": bn, "item": target, "is_fg": is_fg_op})
			p_code, p_name = target, (op.get("sfg_name") or target)
			p_qi = op.get("quality_inspection_template") if op.get("has_quality_inspection") else None
			if is_fg_op:
				fg_boms.append(bn); produced_fg = True; break

		if not produced_fg:
			# No FG-producer op → final FG BOM consuming the last per-FG SFG
			bn, _ = _mk_qty1_bom(fg, p_code, p_name, 1.0, [], {"spec_name": "Assembly"},
			                     p_qi, is_default=is_default, allow_reuse=False)
			created.append({"bom_name": bn, "item": fg, "is_fg": True})
			fg_boms.append(bn)

	frappe.db.commit()
	return {"created_boms": created, "fg_boms": fg_boms}
