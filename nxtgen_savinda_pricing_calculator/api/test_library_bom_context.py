import unittest
from unittest.mock import Mock, patch
import frappe
from nxtgen_savinda_pricing_calculator.api import bom_builder as api


class TestLibraryBOMContext(unittest.TestCase):
    def setUp(self):
        self.library = frappe._dict(name='PL', fg_item='FG', customer='Customer',
            calculation_breakdown='CB', cost_item='CI', check_permission=Mock())
        self.item = frappe._dict(name='FG', item_name='Finished Good', disabled=0,
            has_variants=0, check_permission=Mock())
        self.frappe = Mock()
        self.frappe.get_doc.side_effect = lambda dt, name: self.library if dt == 'Product Library' else self.item
        self.frappe.db.get_value.side_effect = lambda dt, *a, **kw: 1000 if dt == 'Calculation Breakdown' else 'CB-FALLBACK'
        self.frappe.throw.side_effect = ValueError
        patcher = patch.object(api, 'frappe', self.frappe)
        patcher.start()
        self.addCleanup(patcher.stop)

    def context(self):
        return api.get_bom_context.__wrapped__('Product Library', 'PL')

    def test_loads_only_linked_fg_with_library_calculation(self):
        result = self.context()
        self.assertEqual(result['customer'], 'Customer')
        self.assertEqual(result['fg_items'], [dict(item_code='FG', item_name='Finished Good',
            qty=1000, calculation_breakdown='CB', cost_item='CI')])
        self.library.check_permission.assert_called_once_with('read')
        self.item.check_permission.assert_called_once_with('read')

    def test_falls_back_to_cost_item_calculation(self):
        self.library.calculation_breakdown = ''
        self.assertEqual(self.context()['fg_items'][0]['calculation_breakdown'], 'CB-FALLBACK')

    def test_disabled_fg_and_templates_cannot_open_builder(self):
        for field in ('disabled', 'has_variants'):
            self.item[field] = 1
            with self.assertRaises(ValueError):
                self.context()
            self.item[field] = 0

    def test_missing_fg_is_rejected(self):
        self.library.fg_item = ''
        with self.assertRaises(ValueError):
            self.context()
