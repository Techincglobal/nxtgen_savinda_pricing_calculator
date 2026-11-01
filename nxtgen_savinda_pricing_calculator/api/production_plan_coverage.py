"""Sales Order row coverage across non-cancelled Production Plans."""
from collections import defaultdict

import frappe
from frappe.utils import flt


def existing_plans(sales_order):
	names = frappe.db.sql("""
		select pp.name from `tabProduction Plan` pp
		where pp.docstatus < 2 and (
			pp.custom_sales_order = %(so)s
			or exists (select 1 from `tabProduction Plan Sales Order` s
				where s.parent = pp.name and s.sales_order = %(so)s)
			or exists (select 1 from `tabProduction Plan Item` i
				where i.parent = pp.name and i.sales_order = %(so)s)
			or exists (select 1 from `tabProduction Plan Item Reference` r
				where r.parent = pp.name and r.sales_order = %(so)s))
		order by pp.creation asc
	""", {"so": sales_order}, pluck=True)
	return [frappe.get_doc("Production Plan", name) for name in names]


def order_coverage(so, plans):
	"""Quantities are in SO UOM; native planning quantities are converted from stock UOM.

	A ticket row and its native po_items row describe the same reservation. Use
	the larger of the two per plan, never their sum. Legacy tickets without an
	SO row link are allocated by item code to the remaining matching SO rows.
	"""
	items = {row.name: row for row in so.items}
	planned = defaultdict(float)
	existing = []
	for plan in plans:
		if plan.docstatus == 2:
			continue
		ticket, native, refs, legacy = (defaultdict(float) for _ in range(4))
		orders = {row.get("sales_order") for row in (plan.get("sales_orders") or []) if row.get("sales_order")}
		single_order = (plan.get("custom_sales_order") == so.name and not (orders - {so.name})) or orders == {so.name}
		for row in (plan.get("custom_ticket_items") or []):
			if row.get("is_tolerance"):
				continue
			key = row.get("sales_order_item")
			if key in items:
				ticket[key] += max(flt(row.get("qty")), 0)
			elif not key and (row.get("sales_order") == so.name or single_order):
				legacy[row.get("fg_item")] += max(flt(row.get("qty")), 0)
		for rows, target, qty_field in ((plan.get("po_items") or [], native, "planned_qty"),
			(plan.get("prod_plan_references") or [], refs, "qty")):
			for row in rows:
				if target is native and plan.get("prod_plan_references") and not row.get("sales_order"):
					continue  # A combined native row's total belongs to several SO rows.
				key = row.get("sales_order_item")
				if key in items:
					factor = flt(items[key].get("conversion_factor")) or 1
					target[key] += max(flt(row.get(qty_field)), 0) / factor
		per_plan = {key: max(ticket[key], native[key], refs[key]) for key in items}
		for code, quantity in legacy.items():
			already_mapped = sum(qty for key, qty in per_plan.items() if items[key].item_code == code)
			extra = max(quantity - already_mapped, 0)
			for key, item in items.items():
				if item.item_code != code or extra <= 0:
					continue
				available = max(flt(item.qty) - planned[key] - per_plan[key], 0)
				allocated = min(available, extra)
				per_plan[key] += allocated
				extra -= allocated
		if any(per_plan.values()):
			existing.append(plan.name)
		for key, quantity in per_plan.items():
			planned[key] += quantity
	remaining = {key: max(flt(item.qty) - planned[key], 0) for key, item in items.items()
		if flt(item.qty) - planned[key] > 0.000001}
	return {"remaining": remaining, "planned": dict(planned), "existing_plans": existing}
