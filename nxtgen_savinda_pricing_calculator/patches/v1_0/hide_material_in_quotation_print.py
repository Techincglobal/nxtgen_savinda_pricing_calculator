"""Customer quotation must not leak the real material.

The Savinda Quotation print format used to back-fill a blank Material line from the
Calculation Breakdown's base_material Item name. Replace that with the customer-facing
Common Name held on Boards and Papers (or blank) — never the real item name.

Idempotent: only rewrites when the old snippet is still present.
"""

import frappe

OLD = "{% set ns_r.mat = frappe.db.get_value('Item', bmat_code, 'item_name') or bmat_code %}"
NEW = (
	"{% set bmat_iname = frappe.db.get_value('Item', bmat_code, 'item_name') or bmat_code %}"
	"{% set ns_r.mat = frappe.db.get_value('Boards and Papers', bmat_iname, 'common_name') "
	"or frappe.db.get_value('Boards and Papers', {'item': bmat_iname}, 'common_name') "
	"or frappe.db.get_value('Boards and Papers', bmat_code, 'common_name') or '' %}"
)


def execute():
	for name in ("Savinda Quotation", "Savinda Quotation Format"):
		if not frappe.db.exists("Print Format", name):
			continue
		html = frappe.db.get_value("Print Format", name, "html") or ""
		if OLD in html:
			frappe.db.set_value("Print Format", name, "html", html.replace(OLD, NEW))
