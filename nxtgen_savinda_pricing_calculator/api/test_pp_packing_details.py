import unittest
from unittest.mock import patch

import frappe
from nxtgen_savinda_pricing_calculator.api.production_plan import validate_packing_details


class TestPackingDetails(unittest.TestCase):
    def check(self, values, expected_error=None):
        with patch.object(frappe, "throw", side_effect=ValueError) as throw:
            if expected_error:
                with self.assertRaises(ValueError):
                    validate_packing_details(frappe._dict(values))
                self.assertIn(expected_error, throw.call_args.args[0])
            else:
                validate_packing_details(frappe._dict(values))
                throw.assert_not_called()

    def test_offset_needs_only_color_reference(self):
        self.check(dict(custom_pricing_type="Offset"), "Color Reference")
        self.check(dict(custom_pricing_type="Offset", custom_color_ref="Approved sample"))

    def test_flexo_sheet_does_not_need_roll_fields(self):
        values = dict(custom_pricing_type="Flexo", custom_packing_type="Sheet", custom_color_ref="Sample", custom_core_size="3 inch")
        self.check(values)
        self.check(dict(values, custom_core_size=" "), "Core Size")
        self.check(dict(values, custom_packing_type=""), "Packing Type")

    def test_flexo_reel_requires_direction_and_positive_pcs(self):
        values = dict(custom_pricing_type="Flexo", custom_packing_type="Reel", custom_color_ref="Sample", custom_core_size="3 inch", custom_winding_direction="WD1", custom_pcs_per_roll=1000)
        self.check(values)
        self.check(dict(values, custom_winding_direction=""), "Win. Direction")
        for qty in (None, 0, -1):
            self.check(dict(values, custom_pcs_per_roll=qty), "Pcs per Roll")
        self.check(dict(values, custom_color_ref=""), "Color Reference")

    def test_standard_plan_is_unaffected(self):
        self.check({})
