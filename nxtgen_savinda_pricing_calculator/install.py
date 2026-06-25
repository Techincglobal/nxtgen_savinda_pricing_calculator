# Copyright (c) 2026, Techincglobal.com
"""Install-time setup for nxtgen_savinda_pricing_calculator."""

import os

import frappe
from frappe.utils.nestedset import rebuild_tree

ROOT_ITEM_GROUP = "All Item Groups"

# Seed/master data imported ONCE on install only — NOT re-synced on migrate, so
# edits made on the live site (machines, specs, cost facts, rates, config) are
# never overwritten by an update. Order matters: dependencies first.
INSTALL_ONLY_FIXTURES = [
	"item_group.json",          # parent group for the rate items
	"item.json",                # CALC-% rate items (used by Cost Fact Items)
	"cost_fact.json",           # references the rate items above
	"offset_machine.json",
	"offset_ink.json",
	"flexo_foil.json",
	"offset_spec.json",         # references cost facts + machines above
	"costing_configuration.json",  # single — tax/wastage config
]


def before_install():
	"""Ensure the Item Group nested-set tree is healthy before any data import.

	The "Costing Rate Items" group is parented under "All Item Groups"; if the
	root is missing or its nested-set (lft/rgt) is unbuilt, the import fails with
	"cannot unpack non-iterable NoneType object". We repair that here.
	"""
	_ensure_item_group_tree()


def after_install():
	"""Seed master data ONCE on install (kept out of the migrate-time fixture sync)."""
	_ensure_item_group_tree()
	_import_install_only_fixtures()


def after_migrate():
	"""Keep the Item Group tree healthy on every migrate (idempotent).

	NOTE: master/seed data is intentionally NOT re-imported here — migrate must
	not overwrite records edited on the live site.
	"""
	_ensure_item_group_tree()


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


def _import_install_only_fixtures():
	"""Import the seed JSON files from the app's fixtures/ dir, once, on install."""
	from frappe.core.doctype.data_import.data_import import import_doc

	fixtures_dir = frappe.get_app_path("nxtgen_savinda_pricing_calculator", "fixtures")
	for fname in INSTALL_ONLY_FIXTURES:
		path = os.path.join(fixtures_dir, fname)
		if not os.path.exists(path):
			continue
		try:
			import_doc(path)
		except Exception:
			frappe.log_error(
				title=f"pricing_calculator: install import failed ({fname})",
				message=frappe.get_traceback(),
			)
	frappe.db.commit()
