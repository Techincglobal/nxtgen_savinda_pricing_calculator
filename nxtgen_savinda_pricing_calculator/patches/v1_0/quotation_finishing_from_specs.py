"""Quotation print: list only the cost-spec LINKED finishings.

The finishing line previously joined every selected spec from the CB ui_state; it should show
only the Finishing-group specs. Rewire it to the get_cb_finishings jinja helper. Idempotent.
"""

import frappe


def execute():
	old_loop = (
		"{% for sp in cb_specs %}{% if sp.get('spec_name') %}"
		"{% set ns_sp.parts = ns_sp.parts + [sp.get('spec_name')] %}{% endif %}{% endfor %}"
	)
	for name in ("Savinda Quotation", "Savinda Quotation Format"):
		if not frappe.db.exists("Print Format", name):
			continue
		html = frappe.db.get_value("Print Format", name, "html") or ""
		if not html:
			continue
		new = (
			html
			# 1. Finishing line: list only the cost-spec Finishing-group specs. Don't seed
			# ns_r.fin from the (often stale) item.finishing field, so the CB-derived
			# computation below always runs.
			.replace("{% set ns_r.fin = row.finishing or '' %}", "{% set ns_r.fin = '' %}")
			.replace("{% set cb_specs = cb_ui.get('selected_specs') or [] %}", "")
			.replace(
				"{% set ns_sp = namespace(parts=[]) %}",
				"{% set ns_sp = namespace(parts=get_cb_finishings(ns_r.cb)) %}",
			)
			.replace(old_loop, "")
			# 2. Fix a pre-existing crash: User has no 'designation' column — read it from
			# the Employee linked to that user instead (else the whole print errors).
			.replace(
				"frappe.db.get_value('User', doc.sales_person, 'designation')",
				"frappe.db.get_value('Employee', {'user_id': doc.sales_person}, 'designation')",
			)
		)
		if new != html:
			# get_doc + save persists reliably during migrate (a bare set_value on the
			# Print Format html can be lost).
			pf = frappe.get_doc("Print Format", name)
			pf.html = new
			pf.save(ignore_permissions=True)
			frappe.db.commit()
