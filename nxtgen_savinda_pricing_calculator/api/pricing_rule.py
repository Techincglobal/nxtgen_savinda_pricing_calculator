# Copyright (c) 2026, Techincglobal.com
# Qty-tiered Pricing Rules auto-generated when an FG is created. The tier prices are pulled
# from the costing (calculate_qty_break on the FG's Calculation Breakdown), reviewed/edited by
# the user in a popup, then written as native ERPNext Pricing Rules so a Sales Order prices each
# line automatically by qty — instead of copying the rate from the Savinda Quotation.
import json

import frappe
from frappe.utils import flt, getdate, nowdate


def _fmt_q(v):
	v = flt(v)
	return str(int(v)) if v == int(v) else ("%g" % v)


def _tier_qtys_for_cost_item(cost_item):
	"""Qty tiers to price: the Cost Sheet's qty_breaks for the cost sheet that holds this cost
	item, plus the cost item's base order qty. Deduped + ascending."""
	qtys = []
	cs = frappe.db.get_value(
		"Cost Sheet Items", {"item": cost_item}, "parent", order_by="creation desc")
	if cs:
		for r in frappe.get_all(
			"Cost Sheet Qty Break", filters={"parent": cs}, fields=["qty"], order_by="qty asc"):
			if flt(r.qty) > 0:
				qtys.append(flt(r.qty))
	base = flt(frappe.db.get_value("cost Item", cost_item, "item_qty"))
	if base > 0:
		qtys.append(base)
	return sorted(set(qtys))


@frappe.whitelist()
def get_qty_price_tiers(fg_item):
	"""Qty-price tiers for an FG, seeded from the costing. Resolves the FG's cost item + first
	Calculation Breakdown, prices each qty tier via calculate_qty_break, and builds ranges
	(lowest tier starts at qty 1; last tier is open-ended, max_qty = 0). Rates are in base
	(LKR); the popup converts to the quotation currency for display/editing."""
	out = {
		"fg_item": fg_item,
		"item_name": frappe.db.get_value("Item", fg_item, "item_name") or fg_item,
		"cost_item": "", "cb": "", "tiers": [],
	}
	cost_item = frappe.db.get_value("Item", fg_item, "custom_cost_item")
	if not cost_item or not frappe.db.exists("cost Item", cost_item):
		return out
	out["cost_item"] = cost_item
	cb = frappe.db.get_value(
		"Cost Item Calculation", {"parent": cost_item}, "calculation_breakdown", order_by="idx asc")
	if not cb:
		return out
	out["cb"] = cb

	qtys = _tier_qtys_for_cost_item(cost_item)
	if not qtys:
		return out

	from nxtgen_savinda_pricing_calculator.api.offset_calculator import calculate_qty_break
	rows = []
	for q in qtys:
		r = calculate_qty_break(cb, q) or {}
		if r.get("error"):
			continue
		rows.append({
			"qty": flt(q),
			"rate": round(flt(r.get("sell_unit")), 2),
			"unit_cost": round(flt(r.get("unit_cost")), 2),
		})
	rows.sort(key=lambda t: t["qty"])
	n = len(rows)
	for i, t in enumerate(rows):
		t["min_qty"] = 1.0 if i == 0 else t["qty"]
		t["max_qty"] = (rows[i + 1]["qty"] - 1) if i < n - 1 else 0.0
	out["tiers"] = rows
	return out


@frappe.whitelist()
def get_auto_rules(fg_item):
	"""The qty Pricing Rules already auto-generated for an FG (for a view/inspect button)."""
	return frappe.get_all(
		"Pricing Rule",
		filters={"custom_auto_generated": 1, "custom_source_fg": fg_item},
		fields=["name", "min_qty", "max_qty", "rate", "currency", "customer", "valid_from", "valid_upto"],
		order_by="min_qty asc",
	)


@frappe.whitelist()
def create_qty_pricing_rules(fg_item, tiers, customer=None, currency=None,
                             valid_from=None, valid_upto=None, cost_item=None, priority=10):
	"""Create or replace one dated set of qty-tier selling Pricing Rules for an FG.

	Rules with another date range are deliberately retained. This allows a short, high-priority
	promotion (for example, today/tomorrow at LKR 45) to coexist with a longer standard rule
	(for example, six months at LKR 50). Higher priorities win when both rules match.
	"""
	if isinstance(tiers, str):
		tiers = json.loads(tiers or "[]")
	if not fg_item or not frappe.db.exists("Item", fg_item):
		frappe.throw("FG item not found: %s" % fg_item)
	cost_item = cost_item or frappe.db.get_value("Item", fg_item, "custom_cost_item") or ""

	priority = max(1, min(20, int(flt(priority) or 10)))
	valid_from = str(getdate(valid_from)) if valid_from else None
	valid_upto = str(getdate(valid_upto)) if valid_upto else None
	if valid_from and valid_upto and getdate(valid_upto) < getdate(valid_from):
		frappe.throw("Valid Until cannot be before Valid From.")
	rule_customer = customer if customer and frappe.db.exists("Customer", customer) else None

	# Idempotent per *scope*: replace only the same FG/customer/currency/date window. Do not
	# delete another dated rule set, as it may be a temporary override or a future price.
	existing = frappe.get_all(
		"Pricing Rule",
		filters={"custom_auto_generated": 1, "custom_source_fg": fg_item},
		fields=["name", "customer", "currency", "valid_from", "valid_upto"],
	)
	for pr in existing:
		if (
			(pr.customer or None) == rule_customer
			and (pr.currency or None) == (currency or None)
			and (str(pr.valid_from) if pr.valid_from else None) == valid_from
			and (str(pr.valid_upto) if pr.valid_upto else None) == valid_upto
		):
			frappe.delete_doc("Pricing Rule", pr.name, ignore_permissions=True, force=1)

	tiers = sorted([t for t in tiers if flt(t.get("rate")) > 0], key=lambda t: flt(t.get("min_qty")))
	created = []
	for i, t in enumerate(tiers):
		min_qty = flt(t.get("min_qty"))
		max_qty = flt(t.get("max_qty"))
		if max_qty and max_qty < min_qty:
			frappe.throw("Tier {0}: Max Qty ({1}) is below Min Qty ({2}).".format(
				i + 1, _fmt_q(max_qty), _fmt_q(min_qty)))
		pr = frappe.new_doc("Pricing Rule")
		pr.title = "Auto: {0} ({1}-{2})".format(fg_item, _fmt_q(min_qty), _fmt_q(max_qty) if max_qty else "+")
		pr.apply_on = "Item Code"
		pr.append("items", {"item_code": fg_item})
		pr.selling = 1
		pr.buying = 0
		pr.price_or_product_discount = "Price"
		pr.rate_or_discount = "Rate"
		pr.rate = flt(t.get("rate"))
		pr.min_qty = min_qty
		pr.max_qty = max_qty
		# ERPNext selects the highest matching priority. Keep every qty tier in this
		# date window at the same priority; tiers themselves do not overlap.
		pr.priority = str(priority)
		if currency:
			pr.currency = currency
		if rule_customer:
			pr.applicable_for = "Customer"
			pr.customer = rule_customer
		if valid_from:
			pr.valid_from = valid_from
		if valid_upto:
			pr.valid_upto = valid_upto
		pr.custom_auto_generated = 1
		pr.custom_source_fg = fg_item
		pr.custom_cost_item = cost_item
		pr.insert(ignore_permissions=True)
		created.append(pr.name)
	return {"created": created, "count": len(created), "fg_item": fg_item, "priority": priority}


def _active_auto_rule(item_code, qty, customer=None, currency=None, transaction_date=None):
	"""Return the highest-priority active generated rule matching one SO item, or None."""
	date = getdate(transaction_date or nowdate())
	rules = frappe.get_all(
		"Pricing Rule Item Code",
		filters={"item_code": item_code},
		fields=["parent"],
		pluck="parent",
	)
	if not rules:
		return None
	candidates = frappe.get_all(
		"Pricing Rule",
		filters={"name": ["in", rules], "disable": 0, "selling": 1, "custom_auto_generated": 1},
		fields=["name", "rate", "min_qty", "max_qty", "priority", "customer", "currency", "valid_from", "valid_upto"],
	)
	matched = []
	for rule in candidates:
		if rule.customer and rule.customer != customer:
			continue
		if rule.currency and currency and rule.currency != currency:
			continue
		if rule.valid_from and date < getdate(rule.valid_from):
			continue
		if rule.valid_upto and date > getdate(rule.valid_upto):
			continue
		if flt(qty) < flt(rule.min_qty) or (flt(rule.max_qty) and flt(qty) > flt(rule.max_qty)):
			continue
		matched.append(rule)
	return max(matched, key=lambda r: (int(flt(r.priority)), str(r.valid_from or ""), r.name)) if matched else None


def enforce_sales_order_pricing(doc, method=None):
	"""For costing-created FGs, accept only an active matching generated Pricing Rule.

	This intentionally leaves ordinary ERPNext items alone. A costing FG without a valid rule,
	or outside every configured quantity range, is explicitly priced at zero rather than falling
	back to an Item Price, quotation value, or a previous rate.
	"""
	changed = False
	conversion_rate = flt(doc.get("conversion_rate")) or 1
	for row in doc.get("items") or []:
		if not row.item_code or not frappe.db.get_value("Item", row.item_code, "custom_cost_item"):
			continue
		rule = _active_auto_rule(
			row.item_code, row.qty, doc.get("customer"), doc.get("currency"), doc.get("transaction_date"))
		rate = flt(rule.rate) if rule else 0
		row.rate = row.price_list_rate = row.net_rate = rate
		row.amount = row.net_amount = round(flt(row.qty) * rate, 2)
		row.base_rate = row.base_price_list_rate = row.base_net_rate = rate * conversion_rate
		row.base_amount = row.base_net_amount = round(flt(row.qty) * rate * conversion_rate, 2)
		changed = True
	if changed and hasattr(doc, "calculate_taxes_and_totals"):
		doc.calculate_taxes_and_totals()
