"""Backfill the Inquiry's dimensions onto Cost Items, Cost Sheet rows and Calculation
Breakdowns created before the field existed.

Only blank values are filled, so anything typed by hand survives. Idempotent.
"""

import frappe


def execute():
	_fill_cost_items()
	_fill_cost_sheet_rows()
	_fill_carton_sizes()


def _fill_cost_items():
	frappe.db.sql("""
		update `tabcost Item` ci
		join `tabOpportunity` o on o.name = ci.inquiry
		set ci.dimensions = o.custom_dimensions
		where coalesce(ci.dimensions, '') = ''
		  and coalesce(o.custom_dimensions, '') != ''
	""")


def _fill_cost_sheet_rows():
	# The Cost Item is the closer source (a page row can carry its own dimensions);
	# fall back to the Inquiry header for rows whose Cost Item has none.
	frappe.db.sql("""
		update `tabCost Sheet Items` r
		join `tabcost Item` ci on ci.name = r.item
		set r.dimensions = ci.dimensions
		where coalesce(r.dimensions, '') = ''
		  and coalesce(ci.dimensions, '') != ''
	""")
	frappe.db.sql("""
		update `tabCost Sheet Items` r
		join `tabCost Sheet` cs on cs.name = r.parent
		join `tabOpportunity` o on o.name = cs.inquiry
		set r.dimensions = o.custom_dimensions
		where coalesce(r.dimensions, '') = ''
		  and coalesce(o.custom_dimensions, '') != ''
	""")


def _fill_carton_sizes():
	# `ref` holds the Inquiry name, or its subject when typed into the calculator by hand.
	for match_on in ("o.name = cb.ref", "o.custom_subject = cb.ref"):
		frappe.db.sql(f"""
			update `tabCalculation Breakdown` cb
			join `tabOpportunity` o on {match_on}
			set cb.carton_size = o.custom_dimensions
			where coalesce(cb.carton_size, '') = ''
			  and coalesce(o.custom_dimensions, '') != ''
		""")
