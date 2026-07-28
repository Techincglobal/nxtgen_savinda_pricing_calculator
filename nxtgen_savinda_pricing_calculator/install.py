# Copyright (c) 2026, Techincglobal.com
"""Install-time setup for nxtgen_savinda_pricing_calculator."""

import os

import frappe
from frappe.utils.nestedset import rebuild_tree

ROOT_ITEM_GROUP = "All Item Groups"

# ALL seed/master data imported ONCE on install only — NOTHING is re-synced on
# migrate/update, so anything edited on the live site (machines, inks, specs,
# cost facts, items, rates, config, print formats) is never overwritten by an
# app update. Order matters: dependencies first.
INSTALL_ONLY_FIXTURES = [
	"role.json",                # roles first (independent)
	"item_group.json",          # parent group for the rate items
	"item.json",                # CALC-% rate items (used by Cost Fact Items)
	"cost_fact.json",           # references the rate items above
	"offset_machine.json",
	"offset_ink.json",
	"flexo_foil.json",
	"offset_spec.json",         # references cost facts + machines above
	"costing_configuration.json",  # single — tax/wastage config
	"print_format.json",        # print templates
]


def before_install():
	"""Ensure the Item Group nested-set tree is healthy before any data import.

	The "Costing Rate Items" group is parented under "All Item Groups"; if the
	root is missing or its nested-set (lft/rgt) is unbuilt, the import fails with
	"cannot unpack non-iterable NoneType object". We repair that here.

	Roles are also created here so DocTypes whose permissions reference our roles
	(e.g. NPD Request → CS Team / BOM Team) sync cleanly during install.
	"""
	_ensure_item_group_tree()
	_ensure_roles()


def before_migrate():
	"""Create our roles BEFORE DocTypes sync, so DocPerms referencing CS Team /
	BOM Team / Supply Chain / Artwork Approver don't fail on a fresh migrate."""
	_ensure_roles()


def after_install():
	"""Seed master data on install.

	Seeding is NON-DESTRUCTIVE: a record is created only if it does not already
	exist. So even if Frappe Cloud re-runs this on an app update, it will NEVER
	overwrite records edited on the live site (Cost Fact, Offset Ink, Machine,
	Spec, Items, Costing Configuration, etc.) — it only fills in genuinely
	missing ones.
	"""
	_ensure_item_group_tree()
	_ensure_custom_fields()
	_ensure_property_setters()
	_ensure_roles()
	_ensure_workflow()
	_ensure_quotation_workflow()
	_seed_install_only_fixtures()


def after_migrate():
	"""Runs on every migrate/update.

	Master/seed data is intentionally NOT re-imported here. We only keep the
	Item Group tree healthy and ensure our custom fields + roles + workflow exist.
	"""
	_ensure_item_group_tree()
	_ensure_custom_fields()
	_ensure_property_setters()
	_ensure_roles()
	_ensure_workflow()
	_ensure_quotation_workflow()


def _ensure_roles():
	"""Create app roles if missing (idempotent, non-destructive)."""
	for role in ("Artwork Approver", "CS Team", "BOM Team", "Supply Chain", "Quotation Approver"):
		if not frappe.db.exists("Role", role):
			try:
				frappe.get_doc({
					"doctype": "Role", "role_name": role, "desk_access": 1,
				}).insert(ignore_permissions=True)
			except Exception:
				frappe.log_error(
					title=f"pricing_calculator: create role failed ({role})",
					message=frappe.get_traceback(),
				)
		
	frappe.db.commit()


def _ensure_property_setters():
	"""Relax ERPNext's mandatory `po_items` on Production Plan so a plan can be created
	while some items are still awaiting a BOM (the full item list lives in
	custom_ticket_items). Idempotent."""
	try:
		from frappe.custom.doctype.property_setter.property_setter import make_property_setter
		existing = frappe.db.get_value("Property Setter", {
			"doc_type": "Production Plan", "field_name": "po_items", "property": "reqd",
		})
		if not existing:
			make_property_setter(
				"Production Plan", "po_items", "reqd", "0", "Check",
				validate_fields_for_doctype=False,
			)
			frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="pricing_calculator: ensure property setters failed",
			message=frappe.get_traceback(),
		)
	try:
		make_property_setter(
            "Opportunity",          # DocType
            "opportunity_owner",    # Fieldname
            "options",              # Property
            "Employee",             # Value
            "Data",                 # Property type
            validate_fields_for_doctype=False
        )
		frappe.db.commit()

		frappe.clear_cache(doctype="Opportunity")

	except Exception:
		frappe.log_error(
			title="pricing_calculator: update Opportunity Owner field failed",
			message=frappe.get_traceback(),
		)


WORKFLOW_NAME = "Production Plan Approval"


def _ensure_workflow():
	"""Seed / reconcile the Production Plan approval Workflow.

	Artwork is validated on the Cost Sheet, so there is NO artwork state here — the flow is
	CS Team -> BOM Team (BOM Validation) -> Supply Chain (Stock Validation) -> submit. The
	definition is code-owned: an existing Workflow of this name is RECONCILED to match the
	code (states/transitions rebuilt) so definition changes apply on migrate."""
	if not frappe.db.table_exists("Workflow"):
		return
	try:
		# (state, doc_status, owning role, style)
		states = [
			("Draft",                   "0", "CS Team",            ""),
			("BOM Validation",          "0", "BOM Team",           "Warning"),
			("Supply Chain Validation", "0", "Supply Chain",       "Warning"),
			("Approved",                "0", "Supply Chain",       "Success"),
			("Submitted",               "1", "Manufacturing User", "Success"),
			("Rejected",                "0", "CS Team",            "Danger"),
		]
		for state, _ds, _role, style in states:
			if not frappe.db.exists("Workflow State", state):
				frappe.get_doc({
					"doctype": "Workflow State", "workflow_state_name": state, "style": style,
				}).insert(ignore_permissions=True)

		actions = ["Send for BOM", "Confirm BOM", "Reject BOM",
		           "Validate Stock", "Submit Plan", "Reopen"]
		for action in actions:
			if not frappe.db.exists("Workflow Action Master", action):
				frappe.get_doc({
					"doctype": "Workflow Action Master", "workflow_action_name": action,
				}).insert(ignore_permissions=True)

		# (from_state, action, next_state, allowed role)
		transitions = [
			("Draft",                   "Send for BOM",   "BOM Validation",          "CS Team"),
			("BOM Validation",          "Confirm BOM",    "Supply Chain Validation", "BOM Team"),
			("BOM Validation",          "Reject BOM",     "Rejected",                "BOM Team"),
			("Supply Chain Validation", "Validate Stock", "Approved",                "Supply Chain"),
			("Approved",                "Submit Plan",    "Submitted",               "Manufacturing User"),
			("Rejected",                "Reopen",         "Draft",                   "CS Team"),
		]
		wf = (frappe.get_doc("Workflow", WORKFLOW_NAME)
		      if frappe.db.exists("Workflow", WORKFLOW_NAME) else frappe.new_doc("Workflow"))
		wf.workflow_name = WORKFLOW_NAME
		wf.document_type = "Production Plan"
		wf.workflow_state_field = "workflow_state"
		wf.is_active = 1
		wf.send_email_alert = 0
		wf.override_status = 0
		wf.set("states", [])
		wf.set("transitions", [])
		for state, ds, role, _style in states:
			wf.append("states", {"state": state, "doc_status": ds, "allow_edit": role})
		for frm_state, action, to_state, role in transitions:
			wf.append("transitions", {
				"state": frm_state, "action": action, "next_state": to_state,
				"allowed": role, "allow_self_approval": 1,
			})
			# System Manager can drive any transition (admin / recovery).
			wf.append("transitions", {
				"state": frm_state, "action": action, "next_state": to_state,
				"allowed": "System Manager", "allow_self_approval": 1,
			})
		wf.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="pricing_calculator: ensure workflow failed",
			message=frappe.get_traceback(),
		)


QUOTATION_WORKFLOW_NAME = "Savinda Quotation Approval"


def _ensure_quotation_workflow():
	"""Seed the Savinda Quotation approval Workflow (idempotent, non-destructive).

	Conditional on profit margin: >= 10% can be submitted directly; < 10% must go through
	a Quotation Approver. An existing Workflow of this name is left untouched."""
	if not frappe.db.table_exists("Workflow"):
		return
	try:
		# (state, doc_status, owning role, style)
		states = [
			("Draft",            "0", "Sales User",         ""),
			("Pending Approval", "0", "Quotation Approver", "Warning"),
			("Approved",         "1", "Sales User",         "Success"),
			("Rejected",         "0", "Sales User",         "Danger"),
		]
		for state, _ds, _role, style in states:
			if not frappe.db.exists("Workflow State", state):
				frappe.get_doc({
					"doctype": "Workflow State", "workflow_state_name": state, "style": style,
				}).insert(ignore_permissions=True)

		actions = ["Submit Quotation", "Send for Approval", "Approve Quotation",
		           "Reject Quotation", "Reopen Quotation"]
		for action in actions:
			if not frappe.db.exists("Workflow Action Master", action):
				frappe.get_doc({
					"doctype": "Workflow Action Master", "workflow_action_name": action,
				}).insert(ignore_permissions=True)

		if frappe.db.exists("Workflow", QUOTATION_WORKFLOW_NAME):
			return

		# (from_state, action, next_state, allowed role, condition)
		HIGH = "(doc.profit_margin or 0) >= 10"
		LOW  = "(doc.profit_margin or 0) < 10"
		transitions = [
			# Healthy margin (>=10%) — submit directly, no approval.
			("Draft",            "Submit Quotation",  "Approved",         "Sales User",         HIGH),
			# Low margin (<10%) — must be approved.
			("Draft",            "Send for Approval", "Pending Approval", "Sales User",         LOW),
			("Pending Approval", "Approve Quotation", "Approved",         "Quotation Approver", ""),
			("Pending Approval", "Reject Quotation",  "Rejected",         "Quotation Approver", ""),
			("Rejected",         "Reopen Quotation",  "Draft",            "Sales User",         ""),
		]
		wf = frappe.new_doc("Workflow")
		wf.workflow_name = QUOTATION_WORKFLOW_NAME
		wf.document_type = "Savinda Quotation"
		wf.workflow_state_field = "workflow_state"
		wf.is_active = 1
		wf.send_email_alert = 0
		wf.override_status = 0
		for state, ds, role, _style in states:
			wf.append("states", {"state": state, "doc_status": ds, "allow_edit": role})
		for frm_state, action, to_state, role, cond in transitions:
			wf.append("transitions", {
				"state": frm_state, "action": action, "next_state": to_state,
				"allowed": role, "condition": cond, "allow_self_approval": 1,
			})
			# System Manager can drive any transition (admin / recovery).
			wf.append("transitions", {
				"state": frm_state, "action": action, "next_state": to_state,
				"allowed": "System Manager", "condition": cond, "allow_self_approval": 1,
			})
		wf.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="pricing_calculator: ensure quotation workflow failed",
			message=frappe.get_traceback(),
		)


def _ensure_custom_fields():
	"""Custom fields this app adds to standard doctypes (idempotent)."""
	try:
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
		create_custom_fields({
			"Item": [
				{
					"fieldname":    "customer_ref",
					"label":        "Customer Reference Code",
					"fieldtype":    "Data",
					"insert_after": "item_name",
					"description":  "Customer's own code for this item (per FG variant). Used in BOM creation.",
				},
				{
					"fieldname":    "custom_cost_item",
					"label":        "Cost Item",
					"fieldtype":    "Link",
					"options":      "cost Item",
					"insert_after": "customer_ref",
					"description":  "Linked Cost Item — keeps this FG connected to its calculation breakdown for BOM creation. All variants of one product share the same Cost Item.",
				},
				{
					"fieldname":    "custom_product_library",
					"label":        "Product Library",
					"fieldtype":    "Link",
					"options":      "Product Library",
					"insert_after": "custom_cost_item",
					"description":  "Product-library record holding this FG's technical + reference data.",
				},
				{
					"fieldname":    "custom_variant_type",
					"label":        "Variant Type",
					"fieldtype":    "Data",
					"insert_after": "over_billing_allowance",
				},
				{
					"fieldname":    "custom_variant_value",
					"label":        "Variant Value",
					"fieldtype":    "Data",
					"insert_after": "custom_variant_type",
					
				},
				{
					"fieldname": "custom_inquery_item",
					"label": "Inquery Item",
					"fieldtype": "Link",
					"options": "Boards and Papers",
					"insert_after": "Boards and Papers",
					"description":  "Generic material name shown to the customer on the quotation instead of the real material.",
					"depends_on": "eval:doc.item_group=='Boards & Papers'",
				},
			],
			"Delivery Note Item": [
				{
					"fieldname":    "custom_packing",
					"label":        "Packing",
					"fieldtype":    "Link",
					"options":      "Packing",
					"insert_after": "against_sales_order",
					"read_only":    1,
					"description":  "Packing record delivered on this line (set by 'Get from Packing').",
				},
			],
			"Material Request Plan Item": [
				{
					"fieldname":    "custom_wastage_qty",
					"label":        "Wastage Qty",
					"fieldtype":    "Float",
					"insert_after": "quantity",
					"read_only":    1,
					"description":  "Wastage portion included in Quantity (added at production planning). Kept separate for print formats.",
				},
			],
			# Packing details carried from the Cost Sheet flow; editable on the SO per PO.
			"Sales Order Item": [
				{
					"fieldname":    "custom_packing_type",
					"label":        "Packing Type",
					"fieldtype":    "Link",
					"options":      "UOM",
					"insert_after": "item_name",
				},
				{
					"fieldname":    "custom_winding_direction",
					"label":        "Winding Direction",
					"fieldtype":    "Link",
					"options":      "Winding Direction",
					"insert_after": "custom_packing_type",
				},
				{
					"fieldname":    "custom_is_printed",
					"label":        "Is Printed",
					"fieldtype":    "Select",
					"options":      "\nYes\nNo",
					"insert_after": "custom_winding_direction",
				},
				{
					"fieldname":    "custom_pcs_per_role",
					"label":        "PCS per Role/Sheet",
					"fieldtype":    "Int",
					"insert_after": "custom_is_printed",
				},
				{
					"fieldname":    "custom_up",
					"label":        "UP",
					"fieldtype":    "Int",
					"insert_after": "custom_pcs_per_role",
				},
			],
			# Customer-facing common/marketing name for the material (hides the real item).
			"Boards and Papers": [
				{
					"fieldname":    "common_name",
					"label":        "Common Name (Customer-facing)",
					"fieldtype":    "Data",
					"insert_after": "item",
					"description":  "Generic material name shown to the customer on the quotation instead of the real material.",
				},
			],
			# Job Ticket header/print + workflow data on the Production Plan.
			"Production Plan": [
				{"fieldname": "custom_ticket_section", "label": "Job Ticket", "fieldtype": "Section Break", "insert_after": "naming_series"},
				{"fieldname": "custom_ticket_type", "label": "Ticket Type", "fieldtype": "Select", "options": "Job\nNPD", "insert_after": "custom_ticket_section", "in_standard_filter": 1},
				{"fieldname": "custom_pricing_type", "label": "Pricing Type", "fieldtype": "Select", "options": "Offset\nFlexo", "insert_after": "custom_ticket_type", "in_standard_filter": 1},
				{"fieldname": "custom_npd_request", "label": "NPD Request", "fieldtype": "Link", "options": "NPD Request", "insert_after": "custom_pricing_type"},
				{"fieldname": "custom_sales_order", "label": "Source Sales Order", "fieldtype": "Link", "options": "Sales Order", "insert_after": "custom_npd_request", "read_only": 1},
				{"fieldname": "workflow_state", "label": "Workflow State", "fieldtype": "Link", "options": "Workflow State", "insert_after": "custom_sales_order", "read_only": 1, "allow_on_submit": 1, "in_standard_filter": 1, "no_copy": 1},
				{"fieldname": "custom_bom_confirmed", "label": "BOM Confirmed", "fieldtype": "Check", "insert_after": "workflow_state", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_stock_validated", "label": "Stock Validated", "fieldtype": "Check", "insert_after": "custom_bom_confirmed", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_needs_bom", "label": "Has Items Pending BOM", "fieldtype": "Check", "insert_after": "custom_stock_validated", "read_only": 1, "allow_on_submit": 1, "description": "Some fetched items have no BOM yet — sent to the BOM team. Use 'Open BOM Builder' then 'Sync BOMs'."},
				{"fieldname": "custom_ticket_col1", "fieldtype": "Column Break", "insert_after": "custom_needs_bom"},
				{"fieldname": "custom_customer", "label": "Customer", "fieldtype": "Link", "options": "Customer", "insert_after": "custom_ticket_col1"},
				{"fieldname": "custom_customer_name", "label": "Customer Name", "fieldtype": "Data", "insert_after": "custom_customer"},
				{"fieldname": "custom_job_title", "label": "Job Title", "fieldtype": "Data", "insert_after": "custom_customer_name"},
				{"fieldname": "custom_po_no", "label": "PO No", "fieldtype": "Data", "insert_after": "custom_job_title"},
				{"fieldname": "custom_req_date", "label": "Required Date", "fieldtype": "Date", "insert_after": "custom_po_no"},
				{"fieldname": "custom_quote_no", "label": "Quote No", "fieldtype": "Data", "insert_after": "custom_req_date"},
				{"fieldname": "custom_ticket_sec2", "label": "Specifications", "fieldtype": "Section Break", "insert_after": "custom_quote_no"},
				{"fieldname": "custom_job_board", "label": "Job Board", "fieldtype": "Data", "insert_after": "custom_ticket_sec2"},
				{"fieldname": "custom_material", "label": "Material", "fieldtype": "Data", "insert_after": "custom_job_board"},
				{"fieldname": "custom_colors", "label": "Colors", "fieldtype": "Int", "insert_after": "custom_material"},
				{"fieldname": "custom_art_no", "label": "Artwork No", "fieldtype": "Data", "insert_after": "custom_colors"},
				{"fieldname": "custom_art_version", "label": "Artwork Version", "fieldtype": "Data", "insert_after": "custom_art_no"},
				{"fieldname": "custom_color_ref", "label": "Color Reference", "fieldtype": "Data", "insert_after": "custom_art_version"},
				{"fieldname": "custom_ticket_col2", "fieldtype": "Column Break", "insert_after": "custom_color_ref"},
				{"fieldname": "custom_printing_machine", "label": "Printing Machine", "fieldtype": "Data", "insert_after": "custom_ticket_col2"},
				{"fieldname": "custom_finishings", "label": "Finishings", "fieldtype": "Small Text", "insert_after": "custom_printing_machine"},
				{"fieldname": "custom_remarks", "label": "Remarks", "fieldtype": "Small Text", "insert_after": "custom_finishings"},
				# Full job-ticket line list (ALL fetched FGs, incl. those without a BOM yet).
				# po_items holds only the BOM-ready subset (ERPNext requires bom_no there).
				{"fieldname": "custom_items_section", "label": "Job Ticket Items", "fieldtype": "Section Break", "insert_after": "custom_remarks"},
				{"fieldname": "custom_ticket_items", "label": "Items", "fieldtype": "Table", "options": "Job Ticket Item", "insert_after": "custom_items_section"},
				# Manufacturing planning FG lines (print data) — populated by "Get Finished
				# Goods for Manufacture". Offset shows sheet qty/cuts/ups/wastage; Flexo ups/cuts.
				{"fieldname": "custom_offset_planning_sec", "label": "Manufacturing Planning (Offset)", "fieldtype": "Section Break", "insert_after": "custom_ticket_items", "depends_on": "eval:doc.custom_pricing_type=='Offset'"},
				{"fieldname": "custom_offset_planning", "label": "Offset Planning", "fieldtype": "Table", "options": "Production Planning Item", "insert_after": "custom_offset_planning_sec"},
				{"fieldname": "custom_flexo_planning_sec", "label": "Manufacturing Planning (Flexo)", "fieldtype": "Section Break", "insert_after": "custom_offset_planning", "depends_on": "eval:doc.custom_pricing_type=='Flexo'"},
				{"fieldname": "custom_flexo_planning", "label": "Flexo Planning", "fieldtype": "Table", "options": "Production Planning Item", "insert_after": "custom_flexo_planning_sec"},
				# Artwork + approval stamps (allow_on_submit so the workflow can fill them post-submit)
				{"fieldname": "custom_appr_section", "label": "Ticket Approvals", "fieldtype": "Section Break", "insert_after": "custom_remarks", "collapsible": 1},
				{"fieldname": "custom_artwork_status", "label": "Artwork Status", "fieldtype": "Select", "options": "Pending\nApproved\nRejected", "default": "Pending", "insert_after": "custom_appr_section", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_artwork_remarks", "label": "Artwork Remarks", "fieldtype": "Small Text", "insert_after": "custom_artwork_status", "allow_on_submit": 1},
				{"fieldname": "custom_created_by", "label": "Created By", "fieldtype": "Data", "insert_after": "custom_artwork_remarks", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_created_on", "label": "Created On", "fieldtype": "Datetime", "insert_after": "custom_created_by", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_artwork_by", "label": "Artwork Approved By", "fieldtype": "Data", "insert_after": "custom_created_on", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_artwork_on", "label": "Artwork Approved On", "fieldtype": "Datetime", "insert_after": "custom_artwork_by", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_appr_col", "fieldtype": "Column Break", "insert_after": "custom_artwork_on"},
				{"fieldname": "custom_checked_by", "label": "Checked By (Supply Chain)", "fieldtype": "Data", "insert_after": "custom_appr_col", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_checked_on", "label": "Checked On", "fieldtype": "Datetime", "insert_after": "custom_checked_by", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_quoted_by", "label": "Quoted By", "fieldtype": "Data", "insert_after": "custom_checked_on", "allow_on_submit": 1},
				{"fieldname": "custom_quoted_on", "label": "Quoted On", "fieldtype": "Datetime", "insert_after": "custom_quoted_by", "allow_on_submit": 1},
				{"fieldname": "custom_bom_by", "label": "BOM By", "fieldtype": "Data", "insert_after": "custom_quoted_on", "read_only": 1, "allow_on_submit": 1},
				{"fieldname": "custom_bom_on", "label": "BOM On", "fieldtype": "Datetime", "insert_after": "custom_bom_by", "read_only": 1, "allow_on_submit": 1},
			],
			# Per-line geometry for the Job Ticket print (mirrors Job Ticket Item).
			"Production Plan Item": [
				{"fieldname": "custom_product_code", "label": "Product Code", "fieldtype": "Data", "insert_after": "planned_qty"},
				{"fieldname": "custom_size", "label": "Size", "fieldtype": "Data", "insert_after": "custom_product_code"},
				{"fieldname": "custom_batch_no", "label": "Batch/Lot No", "fieldtype": "Data", "insert_after": "custom_size"},
				{"fieldname": "custom_pack_date", "label": "Pack Date", "fieldtype": "Date", "insert_after": "custom_batch_no"},
				{"fieldname": "custom_exp_date", "label": "Exp Date", "fieldtype": "Date", "insert_after": "custom_pack_date"},
				{"fieldname": "custom_full_sheets", "label": "Full Sheets", "fieldtype": "Float", "insert_after": "custom_exp_date"},
				{"fieldname": "custom_cut_sheets", "label": "Cut Sheets", "fieldtype": "Float", "insert_after": "custom_full_sheets"},
				{"fieldname": "custom_full_sheet_size", "label": "Full Sheet Size", "fieldtype": "Data", "insert_after": "custom_cut_sheets"},
				{"fieldname": "custom_cut_sheet_size", "label": "Cut Sheet Size", "fieldtype": "Data", "insert_after": "custom_full_sheet_size"},
				{"fieldname": "custom_cuts", "label": "Cuts", "fieldtype": "Int", "insert_after": "custom_cut_sheet_size"},
				{"fieldname": "custom_ups", "label": "Ups", "fieldtype": "Int", "insert_after": "custom_cuts"},
				{"fieldname": "custom_reel_length", "label": "Reel Length (m)", "fieldtype": "Float", "insert_after": "custom_ups"},
				{"fieldname": "custom_reel_width", "label": "Reel Width (mm)", "fieldtype": "Float", "insert_after": "custom_reel_length"},
				{"fieldname": "custom_reel_area", "label": "Reel Area (sq_m)", "fieldtype": "Float", "insert_after": "custom_reel_width"},
				{"fieldname": "custom_slit_width", "label": "Slit Width (mm)", "fieldtype": "Data", "insert_after": "custom_reel_area"},
				{"fieldname": "custom_repeat_teeth", "label": "Repeat Teeth", "fieldtype": "Data", "insert_after": "custom_slit_width"},
				{"fieldname": "custom_repeat_ups", "label": "Repeat Ups", "fieldtype": "Int", "insert_after": "custom_repeat_teeth"},
				{"fieldname": "custom_across_ups", "label": "Across Ups", "fieldtype": "Int", "insert_after": "custom_repeat_ups"},
				{"fieldname": "custom_across_gaps", "label": "Across Gaps", "fieldtype": "Int", "insert_after": "custom_across_ups"},
				{"fieldname": "custom_material_width", "label": "Across Material Width", "fieldtype": "Data", "insert_after": "custom_across_gaps"},
				{"fieldname": "custom_ups_per_reel", "label": "Ups per Reel", "fieldtype": "Int", "insert_after": "custom_material_width"},
				{"fieldname": "custom_labels_per_reel", "label": "Labels per Reel", "fieldtype": "Int", "insert_after": "custom_ups_per_reel"},
			],
		}, ignore_validate=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="pricing_calculator: ensure custom fields failed",
			message=frappe.get_traceback(),
		)


def _ensure_item_group_tree():
	# ERPNext (which owns Item Group) may not be installed yet.
	if not frappe.db.table_exists("Item Group"):
		return

	# 1. Make sure the conventional root exists.
	if not frappe.db.exists("Item Group", ROOT_ITEM_GROUP):
		try:
			root = frappe.new_doc("Item Group")
			root.item_group_name = ROOT_ITEM_GROUP
			root.is_group = 1
			root.parent_item_group = ""
			root.flags.ignore_mandatory = True
			root.insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(
				title="pricing_calculator: create root Item Group failed",
				message=frappe.get_traceback(),
			)

	# 2. Rebuild lft/rgt so parent lookups during data import succeed.
	try:
		rebuild_tree("Item Group", "parent_item_group")
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="pricing_calculator: rebuild Item Group tree failed",
			message=frappe.get_traceback(),
		)


def _seed_install_only_fixtures():
	"""Create seed records from the app's fixtures/ dir — only the ones missing.

	Reads each JSON file and inserts records that do not already exist. Existing
	records (including any edited on the live site) are left untouched. This is
	deliberately NOT frappe's force-overwrite fixture import.
	"""
	import json

	fixtures_dir = frappe.get_app_path("nxtgen_savinda_pricing_calculator", "fixtures")
	for fname in INSTALL_ONLY_FIXTURES:
		path = os.path.join(fixtures_dir, fname)
		if not os.path.exists(path):
			continue
		try:
			with open(path, encoding="utf-8") as fh:
				records = json.load(fh)
		except Exception:
			frappe.log_error(
				title=f"pricing_calculator: read seed file failed ({fname})",
				message=frappe.get_traceback(),
			)
			continue
		if isinstance(records, dict):
			records = [records]
		for rec in records:
			_create_if_missing(rec)
	frappe.db.commit()


def _create_if_missing(rec):
	"""Insert one fixture record only if it isn't already present."""
	if not isinstance(rec, dict):
		return
	doctype = rec.get("doctype")
	if not doctype or not frappe.db.table_exists(doctype):
		return
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return

	# Strip transient keys that would interfere with a clean insert.
	clean = {
		k: v for k, v in rec.items()
		if k not in ("__islocal", "__unsaved", "modified", "creation", "owner", "modified_by")
	}

	try:
		if meta.issingle:
			# Seed a Single ONLY if it has never been configured — never overwrite.
			if frappe.db.get_singles_dict(doctype):
				return
			single = frappe.get_doc(doctype)
			for k, v in clean.items():
				if k in ("doctype", "name", "idx", "docstatus"):
					continue
				single.set(k, v)
			single.flags.ignore_permissions = True
			single.flags.ignore_mandatory = True
			single.save(ignore_permissions=True)
			return

		name = clean.get("name")
		if name and frappe.db.exists(doctype, name):
			return  # already present — preserve any live edits

		doc = frappe.get_doc(clean)
		doc.flags.ignore_permissions = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"pricing_calculator: seed insert failed ({doctype} {rec.get('name', '')})",
			message=frappe.get_traceback(),
		)
