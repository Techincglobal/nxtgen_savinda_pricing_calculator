import json
import unittest

from nxtgen_savinda_pricing_calculator.api.production_plan import _flexo_technical_defaults, _PL_REVIEW


class TestFlexoLibraryDetails(unittest.TestCase):
    def test_saved_geometry_and_explicit_repeat_fields(self):
        result = _flexo_technical_defaults({"ui_state": json.dumps({"form": {
            "repeat_teeth": 72, "repeat_ups": 5, "product_gap_mm": 0,
            "product_margin_mm": 2, "slit_width": "3 x 75"},
            "calc_result": {"sheet": {"ups": 3, "stickers_per_reel": 8000}}})})
        self.assertEqual(result, dict(pl_cylinder_teeth=72, pl_no_of_repeat=5,
            pl_repeat_gap=0, pl_up_margin=2, pl_slit="3 x 75", pl_across_ups=3))

    def test_missing_cylinder_details_are_not_guessed_from_order_repeats(self):
        result = _flexo_technical_defaults({"ui_state": json.dumps({"calc_result": {
            "sheet": {"stickers_per_reel": 8000, "ups": 4}}})})
        self.assertNotIn("pl_no_of_repeat", result)
        self.assertNotIn("pl_cylinder_teeth", result)
        self.assertEqual(result["pl_across_ups"], 4)
        self.assertEqual(_flexo_technical_defaults({"ui_state": "invalid"}), {})

    def test_shared_popup_fields_are_flexo_only(self):
        fields = dict(_PL_REVIEW)
        for key in ("cylinder_teeth", "no_of_repeat", "repeat_gap", "across_ups", "up_margin", "slit"):
            self.assertEqual(fields[key], "Flexo")
