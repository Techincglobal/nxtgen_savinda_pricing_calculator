"""Quotation print: fix the per-piece price after a currency change.

Since the currency rework, Savinda Quotation Item.selling_price is ALREADY in the transaction
currency and base_selling_price holds the company (LKR) base. The print still computed
`selling_price / conversion_rate`, dividing an already-converted value a second time — so on a
non-base currency it collapsed to ~0.00 ("no value"). Show `base_selling_price / conversion_rate`
(company base → transaction currency), falling back to selling_price. Idempotent.
"""

import frappe


def execute():
	old = "{{ '{:.2f}'.format((row.selling_price or 0) / (doc.conversion_rate or 1)) }}"
	new = ("{{ '{:.2f}'.format(((row.base_selling_price or 0) / (doc.conversion_rate or 1)) "
	       "or (row.selling_price or 0)) }}")
	for name in ("Savinda Quotation", "Savinda Quotation Format"):
		if not frappe.db.exists("Print Format", name):
			continue
		html = frappe.db.get_value("Print Format", name, "html") or ""
		if not html or old not in html:
			continue
		pf = frappe.get_doc("Print Format", name)
		pf.html = html.replace(old, new)
		pf.save(ignore_permissions=True)
		frappe.db.commit()
