import unittest
from unittest.mock import Mock, patch
import frappe
from nxtgen_savinda_pricing_calculator.api import pricing_rule as api


class TestLibraryQuotationPricing(unittest.TestCase):
    def setUp(self):
        self.item = frappe._dict(name='FG-VARIANT', item_name='Box - Red', custom_product_library='PL', check_permission=Mock())
        self.library = frappe._dict(name='PL', fg_item='FG-VARIANT', product_name='Box - Red', quotation_no='SQ', check_permission=Mock())
        self.quote = frappe._dict(name='SQ', docstatus=1, status='Won', currency='USD', conversion_rate=300,
            customer='Customer', check_permission=Mock(), items=[
                frappe._dict(name='row1', item_name='Box', qty=1000, selling_price=.15, unit_cost=30),
                frappe._dict(name='row2', item_name=' BOX ', qty=2000, selling_price=.12, unit_cost=24),
                frappe._dict(name='row3', item_name='Other', qty=500, selling_price=9, unit_cost=1)])
        self.backend = Mock()
        self.backend.get_doc.side_effect = lambda dt, name: {'Item': self.item, 'Product Library': self.library, 'Savinda Quotation': self.quote}[dt]
        self.backend.throw.side_effect = ValueError
        patcher = patch.object(api, 'frappe', self.backend)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tiers(self):
        return api.get_quotation_price_tiers.__wrapped__('FG-VARIANT')

    def test_explicit_row_loads_all_name_group_prices_for_variant(self):
        self.library.pricing_quotation_item = 'row2'
        result = self.tiers()
        self.assertEqual([(r['min_qty'],r['max_qty'],r['rate']) for r in result['tiers']], [(1000,1999,.15),(2000,0,.12)])
        self.assertEqual(result['tiers'][0]['unit_cost'], .1)
        self.assertEqual(result['currency'], 'USD')
        self.library.check_permission.assert_called_with('read')
        self.quote.check_permission.assert_called_with('read')

    def test_name_match_does_not_require_direct_fg_link(self):
        self.item.item_name = 'box'
        self.assertEqual(len(self.tiers()['tiers']), 3)

    def test_direct_link_expands_same_name_quantity_tiers(self):
        self.quote['items'][0].finish_good = self.item.name
        self.assertEqual(len(self.tiers()['tiers']), 3)

    def test_legacy_variant_loads_all_quotation_rows(self):
        result = self.tiers()
        self.assertTrue(result['all_quotation_rows'])
        self.assertEqual({t['quotation_item'] for t in result['tiers']}, {'row1', 'row2', 'row3'})

    def test_same_quantity_different_products_are_not_discarded(self):
        self.quote['items'][2].qty = 1000
        result = self.tiers()
        self.assertEqual(len(result['tiers']), 3)
        self.assertEqual({t['rate'] for t in result['tiers'] if t['qty'] == 1000}, {.15, 9})

    def test_foreign_row_and_cancelled_quotes_rejected(self):
        self.library.pricing_quotation_item = 'wrong-row'
        with self.assertRaises(ValueError):
            self.tiers()
        self.library.pricing_quotation_item = 'row1'
        self.quote.docstatus = 2
        with self.assertRaises(ValueError):
            self.tiers()

    def test_read_permission_still_enforced(self):
        self.quote.check_permission.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.tiers()
