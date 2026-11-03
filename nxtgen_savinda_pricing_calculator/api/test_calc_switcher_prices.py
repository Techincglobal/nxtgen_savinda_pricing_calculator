import unittest
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import offset_calculator as api


class TestSwitcherPrices(unittest.TestCase):
    def test_panel_uses_sheet_qty_and_current_calculation(self):
        def value(doctype, name, fields, **kwargs):
            if doctype == "Cost Item Calculation":
                return "CI" if "calculation_breakdown" in name else "FIRST-CB"
            if doctype == "cost Item":
                return frappe._dict(cost_item_name="Product", item_qty=9999)
            self.fail("Panel must not fetch prices from Calculation Breakdown")

        with patch.object(frappe, "db", Mock()) as db, \
             patch.object(frappe, "get_doc") as get_doc, \
             patch.object(frappe, "get_all", return_value=[frappe._dict(
                 item="CI", qty=8000, unit_price=27.6996, ammount=221596.8,
                 selling_unit_price=32.6509, profit_margin=15)]) as get_all, \
             patch("nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.savinda_quotation.savinda_quotation.find_live_quotation", return_value=None):
            db.exists.return_value = True
            db.get_value.side_effect = value
            result = api.get_calc_switcher.__wrapped__("CS", "CURRENT-CB")
            row = result["qty_breaks"][0]
            self.assertEqual(row["order_qty"], 8000)
            self.assertEqual(row["unit_cost"], 27.6996)
            self.assertEqual(row["quoted_price"], 32.6509)
            self.assertEqual(row["cb"], "CURRENT-CB")
            self.assertTrue(row["is_current"])
            get_doc.return_value.check_permission.assert_called_once_with("read")

    def test_cost_sheet_prices_override_calculation_and_keep_zero(self):
        for unit, selling in [(27.6996, 32.6509), (0, 0)]:
            with patch.object(frappe, "db", Mock()) as db:
                result = api._switcher_price_row("CB", 8000, {
                    "unit_price": unit, "selling_unit_price": selling,
                    "ammount": unit * 8000, "profit_margin": 15,
                })
                self.assertEqual(result["unit_cost"], unit)
                self.assertEqual(result["quoted_price"], selling)
                self.assertEqual(result["profit_margin"], 15)
                db.get_value.assert_not_called()

    def quotation(self, rows):
        return frappe._dict(name="SQ-1", currency="USD", items=rows)

    def test_quote_uses_item_and_quantity_not_name_or_first_row(self):
        quote = self.quotation([
            dict(cost_item="CI-OTHER", qty=8000, selling_price=99),
            dict(cost_item="CI", qty=9000, selling_price=88),
            dict(cost_item="CI", qty=8000, calculation_breakdown="CB", selling_price=0.1234),
        ])
        result = api._switcher_quotation_price(quote, "CI", "CB", 8000)
        self.assertEqual(result["quotation_price"], 0.1234)
        self.assertEqual(result["quotation_currency"], "USD")
        self.assertEqual(result["quotation"], "SQ-1")

    def test_absent_bundle_or_ambiguous_quote_has_no_component_price(self):
        for quote in [None, self.quotation([dict(custom_costing_bundle="CS", qty=8000, selling_price=99)]),
                      self.quotation([dict(cost_item="CI", qty=8000, selling_price=1)] * 2)]:
            self.assertIsNone(api._switcher_quotation_price(quote, "CI", "CB", 8000)["quotation_price"])

    def test_exact_calculation_wins_and_zero_quote_is_not_missing(self):
        quote = self.quotation([
            dict(cost_item="CI", qty=8000, calculation_breakdown="OTHER", selling_price=99),
            dict(cost_item="CI", qty=8000, calculation_breakdown="CB", selling_price=0),
        ])
        self.assertEqual(api._switcher_quotation_price(quote, "CI", "CB", 8000)["quotation_price"], 0)
