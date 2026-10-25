import unittest
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api import pricing_rule as pricing


class TestPricingRuleCurrency(unittest.TestCase):
	def rule(self, name="LKR-RULE", currency="LKR", **values):
		row = frappe._dict(name=name, currency=currency, rate=24.554, min_qty=1, max_qty=10000,
			priority=10, customer="CUSTOMER", valid_from="2026-10-01", valid_upto="2026-10-31")
		row.update(values)
		return row

	def order(self, currency="USD", exchange=300):
		row = frappe._dict(item_code="FG-1", qty=5000, rate=99, amount=495000)
		doc = frappe._dict(currency=currency, conversion_rate=exchange, customer="CUSTOMER",
			transaction_date="2026-10-25", items=[row])
		doc.calculate_taxes_and_totals = Mock()
		return doc, row

	def enforce(self, doc, rules, ordinary=False):
		with patch.object(pricing, "_sales_order_base_currency", return_value="LKR") as base, patch.object(
			frappe, "db", Mock()
		) as db, patch.object(frappe, "get_all") as query:
			db.get_value.return_value = None if ordinary else "CI-1"
			query.side_effect = lambda dt, **kwargs: [r.name for r in rules] if dt == "Pricing Rule Item Code" else rules
			pricing.enforce_sales_order_pricing(doc)
			return base, query

	def test_lkr_rule_converts_to_usd_per_single_unit(self):
		doc, row = self.order()
		self.enforce(doc, [self.rule()])
		self.assertAlmostEqual(row.rate, 24.554 / 300)
		self.assertAlmostEqual(row.price_list_rate, row.rate)
		self.assertAlmostEqual(row.base_rate, 24.554)
		self.assertEqual(row.amount, round(5000 * 24.554 / 300, 2))
		self.assertEqual(row.base_amount, 122770)
		doc.calculate_taxes_and_totals.assert_called_once()

	def test_explicit_usd_rule_wins_over_high_priority_lkr_rule(self):
		doc, row = self.order()
		self.enforce(doc, [self.rule(priority=20), self.rule("USD-RULE", "USD", rate=0.09, priority=1)])
		self.assertEqual(row.rate, 0.09)

		self.assertEqual(row.base_rate, 27)

	def test_eur_rule_does_not_supply_a_usd_rate(self):
		doc, row = self.order()
		self.enforce(doc, [self.rule("EUR-RULE", "EUR", rate=0.1)])
		self.assertEqual(row.rate, 0)
		self.assertEqual(row.amount, 0)

	def test_expired_or_out_of_range_or_wrong_customer_rules_stay_zero(self):
		for values in ({"valid_upto":"2026-10-24"}, {"valid_from":"2026-10-26"},
			{"min_qty":6000}, {"max_qty":4999}, {"customer":"OTHER"}):
			with self.subTest(values=values):
				doc, row = self.order()
				self.enforce(doc, [self.rule(**values)])
				self.assertEqual(row.rate, 0)
				self.assertEqual(row.base_amount, 0)

	def test_expired_usd_rule_can_fall_back_to_valid_lkr_rule(self):
		doc, row = self.order()
		self.enforce(doc, [self.rule("USD-RULE", "USD", valid_upto="2026-10-24"), self.rule()])
		self.assertAlmostEqual(row.rate, 24.554 / 300)

	def test_higher_priority_lkr_promotion_wins_within_fallback_currency(self):
		doc, row = self.order()
		self.enforce(doc, [self.rule(rate=50), self.rule("PROMO", rate=45, priority=20)])
		self.assertEqual(row.rate, 45 / 300)

	def test_lkr_order_is_not_converted_and_does_not_take_usd_rule(self):
		doc, row = self.order(currency="LKR", exchange=1)
		self.enforce(doc, [self.rule(), self.rule("USD-RULE", "USD", rate=0.09, priority=20)])
		self.assertEqual(row.rate, 24.554)
		self.assertEqual(row.base_rate, 24.554)

	def test_general_customer_lkr_rule_applies_when_no_customer_restriction(self):
		doc, row = self.order()
		self.enforce(doc, [self.rule(customer=None)])
		self.assertAlmostEqual(row.rate, 24.554 / 300)

	def test_other_foreign_currency_uses_its_own_exchange_rate(self):
		doc, row = self.order(currency="EUR", exchange=350)
		self.enforce(doc, [self.rule()])
		self.assertAlmostEqual(row.rate, 24.554 / 350)

	def test_ordinary_items_are_unchanged(self):
		doc, row = self.order()
		base, query = self.enforce(doc, [], ordinary=True)
		self.assertEqual(row.rate, 99)
		query.assert_not_called()
		base.assert_not_called()
		doc.calculate_taxes_and_totals.assert_not_called()

	def test_invalid_exchange_rate_is_not_silently_treated_as_one(self):
		for exchange in (None, 0, -1, float("inf"), float("nan")):
			with self.subTest(exchange=exchange):
				doc, _ = self.order(exchange=exchange)
				with patch.object(frappe, "throw", side_effect=ValueError):
					with self.assertRaises(ValueError):
						self.enforce(doc, [self.rule()])

	def test_currency_specific_rule_precedes_legacy_unspecified_currency(self):
		doc, row = self.order()
		self.enforce(doc, [self.rule("LEGACY", None, rate=99, priority=20), self.rule("USD-RULE", "USD", rate=0.09, priority=1)])
		self.assertEqual(row.rate, 0.09)


class TestCurrencyRuleCreation(unittest.TestCase):
	def setUp(self):
		if not hasattr(frappe.local, "flags"):
			frappe.local.flags = frappe._dict(in_test=False)
			self.addCleanup(lambda: delattr(frappe.local, "flags"))

	def create(self, currency="USD", tiers=None, exists=True):
		old_rules = [frappe._dict(name="LKR-OLD", currency="LKR", customer="CUSTOMER", valid_from=None, valid_upto=None),
			frappe._dict(name="USD-OLD", currency="USD", customer="CUSTOMER", valid_from=None, valid_upto=None)]
		pr = Mock()
		pr.name = "NEW-RULE"
		with patch.object(frappe, "db", Mock()) as db, patch.object(frappe, "get_all", return_value=old_rules), patch.object(
			frappe, "new_doc", return_value=pr
		), patch.object(frappe, "delete_doc") as delete, patch(
			"nxtgen_savinda_pricing_calculator.api.fg_approval.require_pricing_reviewer"
		), patch.object(frappe, "throw", side_effect=ValueError):
			db.exists.side_effect = lambda dt, name: exists if dt == "Currency" else True
			db.get_value.return_value = "CI-1"
			result = pricing.create_qty_pricing_rules("FG-1", tiers if tiers is not None else [
				{"min_qty":1, "max_qty":0, "rate":0.081846667}], customer="CUSTOMER", currency=currency)
			return pr, delete.call_args_list, result

	def test_creating_usd_rule_preserves_existing_lkr_rules(self):
		pr, deletes, result = self.create()
		self.assertEqual(pr.currency, "USD")
		self.assertEqual(pr.rate, 0.081846667)
		self.assertEqual([c.args[1] for c in deletes], ["USD-OLD"])
		self.assertEqual(result["count"], 1)

	def test_creating_eur_rule_does_not_replace_usd_or_lkr(self):
		pr, deletes, _ = self.create(currency="EUR")
		self.assertEqual(pr.currency, "EUR")
		self.assertEqual(deletes, [])

	def test_unknown_currency_and_invalid_rates_are_rejected(self):
		with self.assertRaises(ValueError):
			self.create(currency="UNKNOWN", exists=False)
		for rate in (0, -1, float("nan"), float("inf")):
			with self.subTest(rate=rate), self.assertRaises(ValueError):
				self.create(tiers=[{"min_qty":1, "rate":rate}])
