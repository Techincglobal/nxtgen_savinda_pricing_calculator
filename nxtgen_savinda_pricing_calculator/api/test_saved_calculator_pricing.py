import json
import unittest
from unittest.mock import Mock

import frappe
from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.calculation_breakdown.calculation_breakdown import CalculationBreakdown


class TestSavedCalculatorPricing(unittest.TestCase):
    def document(self, sell_total=349995.55):
        form = dict(item_qty=25000, profit_margin=-19.075, tax_sscl=1, tax_vat=0,
                    extra_prod_cost_pct=10, material_rate=2)
        cost = dict(req_qty=10, rate=2, amount=25)
        doc = Mock()
        data = dict(form, ui_state=json.dumps(dict(form=form, calc_result=dict(
            pricing=dict(unit_cost=16.8778, sell_total=sell_total), cost_rows=[cost]))),
            cost_facts=[frappe._dict(cost)])
        doc.get = lambda key: data.get(key)
        doc._saved_calculator_pricing = lambda: CalculationBreakdown._saved_calculator_pricing(doc)
        return doc, data

    def test_negative_margin_keeps_saved_prices_on_save_and_submit(self):
        for total in (349995.55, 0):
            doc, _ = self.document(total)
            CalculationBreakdown.validate(doc)
            self.assertEqual(doc.unit_cost, 16.8778)
            self.assertEqual(doc.selling_price, total)
            doc._calc_cost_fact_amounts.assert_not_called()
            doc._calc_pricing_summary.assert_not_called()
            doc.validate = lambda: CalculationBreakdown.validate(doc)
            CalculationBreakdown.before_submit(doc)
            self.assertEqual(doc.selling_price, total)

    def test_direct_cost_or_quantity_edits_invalidate_snapshot(self):
        doc, data = self.document()
        data['item_qty'] = 1000
        self.assertIsNone(doc._saved_calculator_pricing())
        doc, data = self.document()
        data['cost_facts'][0].rate = 99
        self.assertIsNone(doc._saved_calculator_pricing())
