# Copyright (c) 2026, Techincglobal.com
"""Install-time setup for nxtgen_savinda_pricing_calculator."""

import frappe
from frappe.utils.nestedset import rebuild_tree

ROOT_ITEM_GROUP = "All Item Groups"


def before_install():
    """Ensure the Item Group nested-set tree is healthy before fixtures import.

    The app ships a "Costing Rate Items" Item Group fixture parented under
    "All Item Groups". On some sites the root group is missing or its nested-set
    (lft/rgt) is not built — importing the fixture then fails with
    "cannot unpack non-iterable NoneType object" when Frappe looks up the
    parent's lft/rgt. We repair that here, before sync_fixtures runs.
    """
    _ensure_item_group_tree()


def after_migrate():
    """Keep the Item Group tree healthy on every migrate (idempotent)."""
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

    # 2. Rebuild lft/rgt so parent lookups during fixture import succeed.
    try:
        rebuild_tree("Item Group", "parent_item_group")
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title="pricing_calculator: rebuild Item Group tree failed",
            message=frappe.get_traceback(),
        )
