import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.cost_sheet.cost_sheet import CostSheet


class TestCostSheetCancel(unittest.TestCase):
    def test_active_quotations_block_cancellation(self):
        with patch.object(frappe, 'get_all', return_value=['SQ-DRAFT', 'SQ-SUBMITTED']) as query:
            with patch.object(frappe, 'throw', side_effect=ValueError) as throw:
                with self.assertRaises(ValueError):
                    CostSheet.before_cancel(SimpleNamespace(name='CS-1'))
            self.assertIn('SQ-DRAFT', throw.call_args.args[0])
            self.assertIn('SQ-SUBMITTED', throw.call_args.args[0])
            self.assertEqual(query.call_args.kwargs['filters'], {'cost_sheet': 'CS-1', 'docstatus': ['<', 2]})

    def test_no_active_quotation_allows_cancellation(self):
        with patch.object(frappe, 'get_all', return_value=[]), patch.object(frappe, 'throw') as throw:
            CostSheet.before_cancel(SimpleNamespace(name='CS-1'))
            throw.assert_not_called()
