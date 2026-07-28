"""Currency-aware Savinda Quotation lines.

selling_price now holds the rate in the quotation currency; base_selling_price holds the
company-base (LKR) anchor produced by costing. Existing rows stored the base value in
selling_price, so:
  1. backfill base_selling_price = selling_price, then convert selling_price to the
     quotation currency (selling_price / conversion_rate). conversion_rate defaults to 1,
     so base-currency quotations are unchanged.
  2. repoint the customer print format's per-piece price to the base anchor, so the printed
     (already converted) price is unchanged after the flip.

Idempotent: only rows without a base value are backfilled; the print replace is a no-op
once applied.
"""

import frappe


def execute():
	_backfill_base_price()
	_repoint_print()


def _backfill_base_price():
	if not frappe.db.has_column("Savinda Quotation Item", "base_selling_price"):
		return
	frappe.db.sql(
		"""
		update `tabSavinda Quotation Item` sqi
		join `tabSavinda Quotation` sq on sq.name = sqi.parent
		set sqi.base_selling_price = sqi.selling_price,
		    sqi.selling_price = sqi.selling_price /
		        (case when sq.conversion_rate > 0 then sq.conversion_rate else 1 end),
		    sqi.currency = sq.currency
		where coalesce(sqi.base_selling_price, 0) = 0
		"""
	)


def _repoint_print():
	new = "format(((row.base_selling_price or row.selling_price) or 0) / (doc.conversion_rate or 1)) }}"
	# Match either the conversion form (after quotation_currency_print) or, defensively, the
	# pre-conversion original — both become the base-anchored expression.
	old_forms = (
		"format((row.selling_price or 0) / (doc.conversion_rate or 1)) }}",
		"format(row.selling_price or 0) }}",
	)
	for name in ("Savinda Quotation", "Savinda Quotation Format"):
		if not frappe.db.exists("Print Format", name):
			continue
		html = frappe.db.get_value("Print Format", name, "html") or ""
		if new in html:
			continue
		for old in old_forms:
			if old in html:
				frappe.db.set_value("Print Format", name, "html", html.replace(old, new))
				break
