import unittest
from unittest.mock import patch

from nxtgen_savinda_pricing_calculator.api import offset_calculator as api


class TestFlexoMinimumWastage(unittest.TestCase):
    config = {"flexo_wastage": [(0, 5, .04), (2, 100, .07), (99, 150, .09)],
              "flexo_minimum_wastage": {0: 0, 2: 100, 99: 250}}

    def calculate(self, colors=4, length=1000, override=None):
        with patch.object(api, "_get_config", return_value=self.config):
            return api._calc_flexo(dict(item_qty=10000, reel_width_mm=250,
                product_width_mm=50, product_length_mm=100, no_of_colors=colors,
                reel_length_m=length), override)

    def test_four_colours_compare_setup_plus_percentage_to_minimum(self):
        result = self.calculate()
        self.assertEqual(result["wastage_length"], 250)
        self.assertEqual(result["setup_metrage"], 150)
        self.assertEqual(result["wastage_area"], 62.5)
        self.assertEqual(result["reel_area"], 312.5)

    def test_colour_boundaries(self):
        for colors, minimum in [(0, 0), (1, 100), (2, 100), (3, 0), (8, 250)]:
            with self.subTest(colors=colors):
                self.assertEqual(self.calculate(colors)["minimum_wastage_length"], minimum)

    def test_zero_and_three_keep_setup_and_ignore_configured_minimum(self):
        self.assertEqual(self.calculate(0)["wastage_area"], (5 + 40) * .25)
        self.assertEqual(self.calculate(3)["wastage_area"], (150 + 90) * .25)
        self.assertEqual(self.calculate(3, override=0)["wastage_area"], 150 * .25)

    def test_one_and_two_include_setup_before_minimum(self):
        for colors in (1, 2):
            self.assertEqual(self.calculate(colors)["wastage_area"], 42.5)
            self.assertEqual(self.calculate(colors, length=10000)["wastage_area"], 200)

    def test_percentage_can_exceed_minimum(self):
        self.assertEqual(self.calculate(length=10000)["wastage_length"], 1050)

    def test_spec_override_still_obeys_minimum(self):
        self.assertEqual(self.calculate(override=0)["wastage_length"], 250)
        self.assertEqual(self.calculate(override=30)["wastage_length"], 450)

    def test_configured_zero_disables_minimum(self):
        with patch.dict(self.config, flexo_minimum_wastage={99: 0}):
            self.assertEqual(self.calculate()["wastage_length"], 240)

    def test_missing_dimensions_do_not_charge_minimum(self):
        with patch.object(api, "_get_config", return_value=self.config):
            self.assertEqual(api._calc_flexo({})["reel_area"], 0)

    def test_setup_plus_percentage_exceeds_minimum_without_adding_setup_twice(self):
        result = self.calculate(length=2000)
        self.assertEqual(result["wastage_length"], 330)
        self.assertEqual(result["wastage_area"], 82.5)
        self.assertEqual(result["reel_area"], 582.5)
