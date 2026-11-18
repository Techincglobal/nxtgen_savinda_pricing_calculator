import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import frappe
from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.savinda_quotation.savinda_quotation import SavindaQuotation


class TestCurrentQuotationMargin(unittest.TestCase):
    def setUp(self):
        p=patch.object(frappe, 'get_system_settings', return_value="Banker's Rounding")
        p.start()
        self.addCleanup(p.stop)
    def doc(self, rows, rate=1):
        return SimpleNamespace(meta=Mock(has_field=Mock(return_value=True)),items=[frappe._dict(r) for r in rows],conversion_rate=rate,currency='LKR',profit_margin=50)

    def test_add_remove_and_manual_price_recompute_lowest(self):
        doc=self.doc([dict(item_name='A',unit_cost=80,selling_price=100),dict(item_name='B',unit_cost=90,selling_price=100)])
        SavindaQuotation._apply_lowest_profit_margin(doc)
        self.assertEqual(doc.profit_margin,10)
        doc.items.pop()
        SavindaQuotation._apply_lowest_profit_margin(doc)
        self.assertEqual(doc.profit_margin,20)
        doc.items[0].selling_price=50
        SavindaQuotation._apply_lowest_profit_margin(doc)
        self.assertEqual(doc.profit_margin,-60)
        doc.items=[]
        SavindaQuotation._apply_lowest_profit_margin(doc)
        self.assertEqual(doc.profit_margin,0)

    def test_foreign_currency_and_manual_anchor(self):
        doc=self.doc([dict(item_name='A',unit_cost=240,selling_price=1,base_selling_price=600,is_manually_set=1)],300)
        SavindaQuotation._sync_item_currency(doc)
        SavindaQuotation._apply_lowest_profit_margin(doc)
        self.assertEqual(doc.items[0].base_selling_price,300)
        self.assertEqual(doc.profit_margin,20)

    def test_zero_price_not_replaced_with_old_base(self):
        doc=self.doc([dict(item_name='A',unit_cost=80,selling_price=0,base_selling_price=100,is_manually_set=1)])
        SavindaQuotation._sync_item_currency(doc)
        SavindaQuotation._apply_lowest_profit_margin(doc)
        self.assertEqual(doc.items[0].selling_price,0)
        self.assertEqual(doc.profit_margin,-100)

    def test_bundle_uses_current_rows_not_source_calculation(self):
        doc=self.doc([dict(item_name='Bundle',custom_costing_bundle='CS',unit_cost=80,selling_price=100)])
        SavindaQuotation._apply_lowest_profit_margin(doc)
        self.assertEqual(doc.profit_margin,20)

    def test_usd_rounded_rate_uses_lkr_anchor_without_save_drift(self):
        for manual in (0, 1):
            doc=self.doc([dict(item_name='USD item',unit_cost=35.2724,selling_price=.083,
                               base_selling_price=27.971,is_manually_set=manual)],337.06)
            SavindaQuotation._sync_item_currency(doc)
            SavindaQuotation._apply_lowest_profit_margin(doc)
            self.assertEqual(doc.items[0].base_selling_price,27.971)
            self.assertAlmostEqual(doc.profit_margin,(27.971-35.2724)/27.971*100,places=8)
            doc.items[0].selling_price=round(doc.items[0].selling_price,4)
            SavindaQuotation._sync_item_currency(doc)
            self.assertEqual(doc.items[0].base_selling_price,27.971)

    def test_manual_usd_edit_updates_lkr_margin(self):
        doc=self.doc([dict(item_name='USD item',unit_cost=24,selling_price=.1,
                           base_selling_price=27.971,is_manually_set=1)],300)
        SavindaQuotation._sync_item_currency(doc)
        SavindaQuotation._apply_lowest_profit_margin(doc)
        self.assertEqual(doc.items[0].base_selling_price,30)
        self.assertEqual(doc.profit_margin,20)
