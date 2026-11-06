# Copyright (c) 2026, Techincglobal.com
# Qty-tiered Pricing Rules auto-generated when an FG is created. The tier prices are pulled
# from the costing (calculate_qty_break on the FG's Calculation Breakdown), reviewed/edited by
# the user in a popup, then written as native ERPNext Pricing Rules so a Sales Order prices each
# line automatically by qty — instead of copying the rate from the Savinda Quotation.
import json
import math

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


def _pricing_item_name(value):
	return " ".join((value or "").split()).casefold()


def _quotation_pricing_rows(quote, item, library=None):
	"""An explicit row identifies a product group, not just one quantity tier."""
	library = library or {}
	rows = quote.get("items") or []
	selected = library.get("pricing_quotation_item")
	if selected:
		anchor = next((r for r in rows if r.name == selected), None)
		if not anchor:
			frappe.throw("Pricing Quotation Item does not belong to the linked quotation. Select it again in Product Library.")
		key = _pricing_item_name(anchor.get("item_name"))
		return [r for r in rows if _pricing_item_name(r.get("item_name")) == key] if key else [anchor]
	if library.get("quotation_no"):
		return rows  # Older libraries without a source row show the complete quotation.
	linked = [r for r in rows if r.get("finish_good") == item.name]
	keys = {_pricing_item_name(r.get("item_name")) for r in linked} if linked else {
		_pricing_item_name(item.get("item_name")), _pricing_item_name(library.get("product_name"))}
	keys.discard("")
	matched = [r for r in rows if _pricing_item_name(r.get("item_name")) in keys]
	if not linked and len({_pricing_item_name(r.get("item_name")) for r in matched}) > 1:
		frappe.throw("More than one quotation product matches. Select Pricing Quotation Item in Product Library.")
	return matched or linked


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_pricing_quotation_items(doctype, txt, searchfield, start, page_len, filters):
	quotation = (filters or {}).get("quotation")
	if not quotation:
		return []
	quote = frappe.get_doc("Savinda Quotation", quotation)
	quote.check_permission("read")
	text = (txt or "").casefold()
	rows = [(r.name, r.item_name, _fmt_q(r.qty)) for r in quote.get("items") or []
		if text in (r.name + " " + (r.item_name or "")).casefold()]
	return rows[int(start):int(start) + int(page_len)]


@frappe.whitelist()
def get_quotation_price_tiers(fg_item):
	"""Use the library's quotation/product group; retain direct-FG lookup as fallback."""
	item = frappe.get_doc("Item", fg_item)
	item.check_permission("read")
	pl_name = item.get("custom_product_library") or frappe.db.get_value("Product Library", {"fg_item": fg_item}, "name")
	library = frappe.get_doc("Product Library", pl_name) if pl_name else None
	if library:
		library.check_permission("read")
		if library.get("fg_item") != fg_item:
			frappe.throw("The linked Product Library belongs to a different FG.")
	quote_name = library.get("quotation_no") if library else None
	if not quote_name:
		parents = frappe.get_all("Savinda Quotation Item", filters={"finish_good": fg_item,
			"parenttype": "Savinda Quotation"}, pluck="parent")
		quotes = frappe.get_list("Savinda Quotation", filters={"name": ["in", parents or [""]],
			"docstatus": ["<", 2], "status": ["not in", ["Lost", "Cancelled"]]},
			fields=["name"], order_by="modified desc, name desc", limit_page_length=1)
		quote_name = quotes[0].name if quotes else None
	if not quote_name:
		frappe.throw("Set Quotation No and Pricing Quotation Item in the FG's Product Library to load pricing.")
	quote = frappe.get_doc("Savinda Quotation", quote_name)
	quote.check_permission("read")
	if quote.docstatus == 2 or quote.get("status") in ("Lost", "Cancelled"):
		frappe.throw("Select an active quotation in Product Library; the selected quotation is cancelled or lost.")
	rows = _quotation_pricing_rows(quote, item, library)
	if not rows:
		frappe.throw("No matching product name was found in the quotation. Select Pricing Quotation Item in Product Library to choose the pricing group.")
	all_rows = bool(library and library.get("quotation_no") and not library.get("pricing_quotation_item"))
	by_qty = {}
	for row in rows:
		if flt(row.get("qty")) > 0:
			# Keep every quotation line for legacy libraries; never hide another product
			# merely because it has the same quantity. Explicit groups retain last-rate wins.
			key = row.name if all_rows else flt(row.qty)
			by_qty[key] = {"qty": flt(row.qty), "rate": flt(row.get("selling_price")),
				"quotation_item": row.name, "quotation_item_name": row.get("item_name") or "",
				"unit_cost": flt(row.get("unit_cost")) / (flt(quote.get("conversion_rate")) or 1)}
	tiers = sorted(by_qty.values(), key=lambda t: (_pricing_item_name(t["quotation_item_name"]) if all_rows else "", t["qty"]))
	for tier in tiers:
		next_qtys = [t["qty"] for t in tiers if t["qty"] > tier["qty"] and
			(not all_rows or _pricing_item_name(t["quotation_item_name"]) == _pricing_item_name(tier["quotation_item_name"]))]
		tier.update(min_qty=tier["qty"], max_qty=min(next_qtys) - 1 if next_qtys else 0)
	return {"tiers": tiers, "all_quotation_rows": all_rows, "quotation": quote.name, "customer": quote.get("customer"),
		"currency": quote.get("currency"), "conversion_rate": quote.get("conversion_rate"),
		"valid_till": quote.get("valid_till")}


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
	bundle = frappe.db.get_value("Item", fg_item, "custom_costing_bundle")
	if bundle:
		from nxtgen_savinda_pricing_calculator.api.costing_bundle import pricing_tiers
		out["tiers"] = pricing_tiers(bundle)
		return out
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
			"rate": flt(r.get("sell_unit")),
			"unit_cost": flt(r.get("unit_cost")),
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
	from nxtgen_savinda_pricing_calculator.api.fg_approval import require_pricing_reviewer
	require_pricing_reviewer(fg_item)
	if currency and not frappe.db.exists("Currency", currency):
		frappe.throw("Select a valid Pricing Rule Currency.")
	if not isinstance(tiers, list) or not tiers:
		frappe.throw("Enter at least one pricing tier.")
	for tier in tiers:
		if not isinstance(tier, dict) or not math.isfinite(flt(tier.get("rate"))) or flt(tier.get("rate")) <= 0:
			frappe.throw("Every pricing tier must have a positive, finite per-item rate.")
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
	# Explicit transaction-currency rules win over legacy currency-unspecified rules.
	return max(matched, key=lambda r: (
		bool(currency and r.currency == currency), int(flt(r.priority)), str(r.valid_from or ""), r.name
	)) if matched else None


def _sales_order_base_currency(doc):
	"""The SO exchange rate is company base per transaction-currency unit."""
	if doc.get("company"):
		currency = frappe.db.get_value("Company", doc.company, "default_currency")
		if currency:
			return currency
	from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.savinda_quotation.savinda_quotation import _company_currency
	return _company_currency()


def validate_sales_order_fg_rates(doc, method=None):
	"""After validate resolves Pricing Rules, require positive FG rates on submit."""
	invalid = []
	for index, row in enumerate(doc.get("items") or [], 1):
		if not row.item_code or not (
			frappe.db.get_value("Item", row.item_code, "custom_cost_item")
			or frappe.db.get_value("Item", row.item_code, "custom_costing_bundle")
		):
			continue
		rate = flt(row.get("rate"))
		if not math.isfinite(rate) or rate <= 0:
			invalid.append("Row #{0}: {1}".format(row.get("idx") or index,
				frappe.utils.escape_html(row.item_code)))
	if invalid:
		frappe.throw(
			"Cannot submit Sales Order: the following costing FG items need a positive selling rate.<br>"
			+ "<br>".join(invalid)
			+ "<br>Set an active Pricing Rule matching the customer, quantity, currency and Sales Order date, "
			"then save the order and submit again.",
			title="FG Pricing Required",
		)


def enforce_sales_order_pricing(doc, method=None):
	"""For costing-created FGs, accept only an active matching generated Pricing Rule.

	This intentionally leaves ordinary ERPNext items alone. A costing FG without a valid rule,
	or outside every configured quantity range, is explicitly priced at zero rather than falling
	back to an Item Price, quotation value, or a previous rate.
	Prefer a transaction-currency rule; otherwise convert a matching company-base rule
	(LKR on Savinda sites). Never reuse a rate from an unrelated foreign currency.
	"""
	changed = False
	base_currency = None
	for row in doc.get("items") or []:
		if not row.item_code or not (frappe.db.get_value("Item", row.item_code, "custom_cost_item") or frappe.db.get_value("Item", row.item_code, "custom_costing_bundle")):
			continue
		if base_currency is None:
			base_currency = _sales_order_base_currency(doc)
		currency = doc.get("currency") or base_currency
		conversion_rate = flt(doc.get("conversion_rate")) if currency != base_currency else 1
		if not math.isfinite(conversion_rate) or conversion_rate <= 0:
			frappe.throw("Set a positive Sales Order exchange rate ({0} per 1 {1}) before fetching FG pricing.".format(base_currency, currency))
		rule = _active_auto_rule(
			row.item_code, row.qty, doc.get("customer"), currency, doc.get("transaction_date"))
		convert_base_rule = False
		if not rule and currency != base_currency:
			rule = _active_auto_rule(
				row.item_code, row.qty, doc.get("customer"), base_currency, doc.get("transaction_date"))
			convert_base_rule = bool(rule)
		rate = flt(rule.rate) if rule else 0
		if convert_base_rule:
			rate /= conversion_rate
		row.rate = row.price_list_rate = row.net_rate = rate
		row.amount = row.net_amount = round(flt(row.qty) * rate, 2)
		row.base_rate = row.base_price_list_rate = row.base_net_rate = rate * conversion_rate
		row.base_amount = row.base_net_amount = round(flt(row.qty) * rate * conversion_rate, 2)
		changed = True
	if changed and hasattr(doc, "calculate_taxes_and_totals"):
		doc.calculate_taxes_and_totals()
