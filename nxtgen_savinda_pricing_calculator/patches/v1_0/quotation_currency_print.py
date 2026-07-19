"""Show quotation prices in the quotation currency on the customer print format.

Costing stays in company base (LKR); the Savinda Quotation carries a currency +
conversion_rate (LKR per 1 unit). Convert the printed per-piece price and one-time
charges, and label them with the currency. Idempotent + backward-compatible
(conversion_rate defaults to 1 → identical output for base-currency quotations).
"""

import frappe

REPLACEMENTS = [
	(
		"Price Per Piece (LKR)",
		"Price Per Piece ({{ doc.currency or 'LKR' }})",
	),
	(
		"format(row.selling_price or 0) }}",
		"format((row.selling_price or 0) / (doc.conversion_rate or 1)) }}",
	),
	(
		"format(doc.plate_charges) }} LKR",
		"format((doc.plate_charges or 0) / (doc.conversion_rate or 1)) }} {{ doc.currency or 'LKR' }}",
	),
	(
		"format(doc.die_cutter_charges) }}",
		"format((doc.die_cutter_charges or 0) / (doc.conversion_rate or 1)) }} {{ doc.currency or 'LKR' }}",
	),
]


def execute():
	for name in ("Savinda Quotation", "Savinda Quotation Format"):
		if not frappe.db.exists("Print Format", name):
			continue
		html = frappe.db.get_value("Print Format", name, "html") or ""
		changed = False
		for old, new in REPLACEMENTS:
			if old in html and new not in html:
				html = html.replace(old, new)
				changed = True
		if changed:
			frappe.db.set_value("Print Format", name, "html", html)
