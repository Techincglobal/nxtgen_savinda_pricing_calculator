import unittest
from unittest.mock import patch

import frappe
from nxtgen_savinda_pricing_calculator.api.offset_calculator import _calc_spec_ink_cost


class TestOffsetInkCycles(unittest.TestCase):
    def test_cycles_do_not_multiply_ink_but_wastage_and_coverage_apply(self):
        ink = frappe._dict(consumption_per_sqinch=0.00001, price_per_kg=2000,
                           min_qty=0, min_value=0)
        with patch.object(frappe, "get_cached_doc", return_value=ink):
            for cycles in (1, 2, 3):
                assignment = {"cycles": cycles, "inks": [{"ink_name": "Black", "percentage": 50}]}
                rows, cost = _calc_spec_ink_cost("Print", assignment,
                    {"cut_sheet_l": 10, "cut_sheet_w": 20},
                    {"cut_sheet_qty": 900, "req_cut_sheets": 1000})
                self.assertEqual(rows[0]["req_qty"], 1)
                self.assertEqual(cost, 2000)

    def test_ink_minimums_still_apply(self):
        ink = frappe._dict(consumption_per_sqinch=0.00001, price_per_kg=2000,
                           min_qty=0.5, min_value=1500)
        with patch.object(frappe, "get_cached_doc", return_value=ink):
            rows, cost = _calc_spec_ink_cost("Print",
                {"cycles": 2, "inks": [{"ink_name": "Black", "percentage": 100}]},
                {"cut_sheet_l": 10, "cut_sheet_w": 20}, {"req_cut_sheets": 100})
        self.assertEqual(rows[0]["req_qty"], 0.5)
        self.assertEqual(cost, 1500)
