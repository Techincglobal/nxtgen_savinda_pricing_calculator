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
