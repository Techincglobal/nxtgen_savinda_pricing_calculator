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
	"""
	_ensure_item_group_tree()


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
	_ensure_roles()
	_seed_install_only_fixtures()


def after_migrate():
	"""Runs on every migrate/update.

	Master/seed data is intentionally NOT re-imported here. We only keep the
	Item Group tree healthy and ensure our custom fields + roles exist.
	"""
	_ensure_item_group_tree()
	_ensure_custom_fields()
	_ensure_roles()


def _ensure_roles():
	"""Create app roles if missing (idempotent, non-destructive)."""
	for role in ("Artwork Approver",):
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
