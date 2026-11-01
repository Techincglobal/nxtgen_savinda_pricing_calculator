"""Library-first BOM inputs, with no writes to library/costing masters."""
import json
import unittest
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import bom_builder, offset_calculator


class TestBOMBuilderLibrary(unittest.TestCase):
	def library(self, **values):
		return frappe._dict(fg_item="FG-TEST", check_permission=Mock(), **values)

	def inputs(self, library, pricing="Offset"):
		with patch.object(frappe.db, "get_value", return_value="PL-TEST"), patch.object(frappe, "get_doc", return_value=library):
			return bom_builder._library_bom_inputs("FG-TEST", pricing)

	def test_offset_library_values_are_mapped(self):
		values = self.inputs(self.library(no_of_ups=8, no_of_colors=4,
			full_sheet_size="31 x 43", cut_sheet_size="15.5 * 21.5", product_size="100x80x40"))
		self.assertEqual(values, {"no_of_ups":8,"no_of_colors":4,"full_sheet_l":31,"full_sheet_w":43,
			"cut_sheet_l":15.5,"cut_sheet_w":21.5,"carton_size":"100x80x40"})
		self.assertNotIn("no_of_cuts", values)

	def test_zero_blank_and_invalid_values_do_not_erase_costing(self):
		self.assertEqual(self.inputs(self.library(no_of_ups=0, no_of_colors=0,
			full_sheet_size="31 x 0", cut_sheet_size="Set 1: 24 x 17", product_size=" ")), {})

	def test_size_pairs_are_strict_and_support_common_inch_formats(self):
		for value in ['31 x 43', '31*43', '31 × 43', '31" x 43"', '31 x 43 inches']:
			self.assertEqual(bom_builder._library_sheet_size(value), (31, 43))
		for value in ['', '31', '31 x 0', '-31 x 43', '310 x 430 mm', '31 x 43 x 2', 'Set1-31x43']:
			self.assertIsNone(bom_builder._library_sheet_size(value))

	def test_optional_site_cuts_field_is_used_if_populated(self):
		self.assertEqual(self.inputs(self.library(no_of_cuts=4)), {"no_of_cuts":4})

	def test_flexo_legacy_library_length_cannot_override_product_length(self):
		values = self.inputs(self.library(width_mm=333, length_mm=1124, full_sheet_size="31x43"), "Flexo")
		self.assertEqual(values, {"reel_width_mm":333})
		self.assertNotIn("product_width_mm", values)
		self.assertNotIn("product_length_mm", values)

	def test_flexo_product_length_stays_separate_and_can_be_edited(self):
		state = {"form": {"pricing_type": "Flexo", "product_length_mm": 80,
			"reel_length_m": 1124, "reel_width_mm": 333}, "selected_specs": []}
		cb = frappe._dict(ui_state=json.dumps(state), pricing_type="Flexo")
		for overrides, expected in ((None, 80), ({"product_length_mm": 90}, 90)):
			with self.subTest(overrides=overrides), \
				patch.object(frappe, "get_doc", return_value=cb), \
				patch.object(frappe.db, "get_value", return_value=""), \
				patch.object(bom_builder, "_library_bom_inputs", return_value={"reel_width_mm": 333}), \
				patch.object(offset_calculator, "calculate", return_value={"sheet": {"reel_area": 400, "ups": 4}, "cost_rows": []}):
				result = bom_builder.get_bom_data("CB", 1000, "FG", overrides)
				self.assertEqual(result["drivers"]["product_length_mm"], expected)
				self.assertEqual(result["drivers"]["reel_length_m"], 1124)

	def test_missing_library_preserves_original_behaviour(self):
		with patch.object(frappe.db, "get_value", return_value=None), patch.object(frappe, "get_doc") as get_doc:
			self.assertEqual(bom_builder._library_bom_inputs("FG-TEST", "Offset"), {})
			get_doc.assert_not_called()

	def test_wrong_fg_library_is_not_used(self):
		pl = self.library(no_of_ups=8)
		pl.fg_item = "FG-OTHER"
		self.assertEqual(self.inputs(pl), {})

	def test_library_back_reference_used_when_item_link_empty(self):
		pl = self.library(no_of_ups=8)
		with patch.object(frappe.db, "get_value", side_effect=[None, "PL-TEST"]) as query, patch.object(frappe, "get_doc", return_value=pl):
			self.assertEqual(bom_builder._library_bom_inputs("FG-TEST", "Offset"), {"no_of_ups":8})
		self.assertEqual(query.call_args.args, ("Product Library", {"fg_item":"FG-TEST"}, "name"))

	def calculate_builder(self, overrides=None):
		state = {"form":{"pricing_type":"Offset","no_of_cuts":2,"no_of_ups":2,
			"full_sheet_l":31,"full_sheet_w":43,"cut_sheet_l":24,"cut_sheet_w":17,"item_qty":100}, "selected_specs":[]}
		cb = frappe._dict(ui_state=json.dumps(state), pricing_type="Offset")
		seen = {}
		def calculate(payload):
			seen.update(json.loads(payload)["form"])
			return {"sheet":offset_calculator._calc_sheet(seen), "cost_rows":[]}
		with patch.object(frappe, "get_doc", return_value=cb), patch.object(frappe.db, "get_value", return_value=""), patch.object(
			bom_builder, "_library_bom_inputs", return_value={"no_of_ups":8,"cut_sheet_l":15.5,"cut_sheet_w":21.5}), patch.object(
			offset_calculator, "calculate", side_effect=calculate), patch.object(offset_calculator, "_get_config", return_value={"offset_wastage_pct":.05,"offset_wastage_min":500}):
			result = bom_builder.get_bom_data("CB-TEST", 1000, "FG-TEST", overrides)
		return result, seen

	def test_sheet_counts_are_recalculated_from_library_ups(self):
		result, seen = self.calculate_builder()
		self.assertEqual(seen["item_qty"], 1000)
		self.assertEqual(seen["no_of_cuts"], 2)
		self.assertEqual(result["drivers"]["no_of_ups"], 8)
		self.assertEqual(result["sheet"]["cut_sheet_ups"], 4)
		self.assertEqual(result["sheet"]["cut_sheet_qty"], 250)
		self.assertEqual(result["drivers"]["cut_sheet_l"], 15.5)

	def test_manual_builder_inputs_win_over_library(self):
		result, seen = self.calculate_builder({"no_of_ups":12,"cut_sheet_l":10})
		self.assertEqual(seen["no_of_ups"], 12)
		self.assertEqual(result["sheet"]["cut_sheet_ups"], 6)
		self.assertEqual(result["drivers"]["cut_sheet_l"], 10)

	def test_bom_stamped_fields_use_library_with_fallback_for_missing_cuts(self):
		with patch.object(bom_builder, "_find_cb_for_fg", return_value="CB-TEST"), patch.object(
			bom_builder.jt_api, "_cb_fields", return_value=frappe._dict(pricing_type="Offset",no_of_ups=2,no_of_cuts=2,
				full_sheet_l=31,full_sheet_w=43,cut_sheet_l=24,cut_sheetw=17)), patch.object(
			bom_builder, "_library_bom_inputs", return_value={"no_of_ups":8,"cut_sheet_l":15.5,"cut_sheet_w":21.5}), patch.object(frappe.db,"get_value",return_value=None):
			values = bom_builder._bom_calc_field_values("FG-TEST")
		self.assertEqual(values["custom_no_of_ups"], 8)
		self.assertEqual(values["custom_no_of_cuts"], 2)
		self.assertEqual(values["custom_cut_sheet_ups"], 4)
		self.assertEqual(values["custom_cut_sheet_size"], "15.5 x 21.5")

	def test_bom_details_use_selected_calculation_and_reviewed_offset_values(self):
		with patch.object(bom_builder.jt_api, "_cb_fields", return_value={"pricing_type": "Offset",
			"no_of_ups": 2, "no_of_cuts": 1, "carton_size": "OLD"}) as cb, \
			patch.object(bom_builder, "_library_bom_inputs", return_value={"cut_sheet_l": 24, "cut_sheet_w": 17}), \
			patch.object(frappe.db, "get_value", return_value=None):
			values = bom_builder._bom_calc_field_values("FG", "SELECTED-CB",
				{"no_of_ups": 8, "no_of_cuts": 2, "cut_sheet_l": 15.5, "cut_sheet_w": 21.5,
				 "full_sheet_l": 31, "full_sheet_w": 43, "carton_size": "150 x 40 x 60"}, 5000)
		cb.assert_called_once_with("SELECTED-CB")
		self.assertEqual(values["custom_cut_sheet_size"], "15.5 x 21.5")
		self.assertEqual(values["custom_full_sheet_size"], "31.0 x 43.0")
		self.assertEqual(values["custom_carton_size"], "150 x 40 x 60")
		self.assertEqual(values["custom_cut_sheet_ups"], 4)
		self.assertEqual(values["custom_base_item_qty"], 5000)

	def test_flexo_bom_keeps_product_and_reel_dimensions_separate(self):
		form = {"pricing_type": "Flexo", "reel_width_mm": 333, "product_width_mm": 70,
			"product_length_mm": 80, "product_gap_mm": 0, "product_margin_mm": 0,
			"no_of_colors": 4, "item_qty": 1000}
		with patch.object(bom_builder.jt_api, "_cb_fields", return_value={"pricing_type": "Flexo",
			"ui_state": json.dumps({"form": form})}), \
			patch.object(bom_builder, "_library_bom_inputs", return_value={}), \
			patch.object(offset_calculator, "_get_config", return_value={"flexo_wastage": [(99, 0, 0)]}), \
			patch.object(frappe.db, "get_value", return_value=None):
			values = bom_builder._bom_calc_field_values("FG", "CB", {"product_length_mm": 100, "carton_size": "70 x 100"}, 2000)
		self.assertEqual(values["custom_product_width_mm"], 70)
		self.assertEqual(values["custom_product_length_mm"], 100)
		self.assertEqual(values["custom_reel_width_mm"], 333)
		self.assertEqual(values["custom_reel_length_m"], 50)
		self.assertEqual(values["custom_no_of_ups"], 4)
		self.assertEqual(values["custom_carton_size"], "70 x 100")
		self.assertEqual(values["custom_flexo_repeat_count"], 500)
		self.assertAlmostEqual(values["custom_length_per_repeat_m"], .1)

	def test_flexo_10000_labels_four_ups_consumes_length_per_2500_repeats(self):
		form = {"pricing_type": "Flexo", "base_material": "FILM", "reel_width_mm": 330,
			"product_width_mm": 70, "product_length_mm": 80, "product_gap_mm": 0,
			"product_margin_mm": 0, "no_of_colors": 4, "item_qty": 10000}
		cb = frappe._dict(ui_state=json.dumps({"form": form, "selected_specs": []}), pricing_type="Flexo")
		for uom, expected in (("Meter", 240), ("M2", 79.2), ("sqmt", 79.2),
			("sqm", 79.2), ("Sq. M", 79.2), ("Square Metre", 79.2)):
			def get_value(dt, name, field, **kwargs):
				return uom if dt == "Item" and field == "stock_uom" else ""
			def calculate(payload):
				return {"sheet": offset_calculator._calc_flexo(json.loads(payload)["form"]), "cost_rows": []}
			with self.subTest(uom=uom), patch.object(frappe, "get_doc", return_value=cb), \
				patch.object(frappe.db, "get_value", side_effect=get_value), \
				patch.object(bom_builder, "_library_bom_inputs", return_value={}), \
				patch.object(offset_calculator, "_get_config", return_value={"flexo_wastage": [(99, 0, .2)]}), \
				patch.object(offset_calculator, "calculate", side_effect=calculate):
				result = bom_builder.get_bom_data("CB", 10000, "FG")
			self.assertAlmostEqual(result["base_material_qty"], expected)
			self.assertEqual(result["base_material_uom"], uom)
			self.assertEqual(result["after_print_qty"], 240)
			self.assertEqual(result["sheet"]["bom_repeat_count"], 2500)
			self.assertAlmostEqual(result["sheet"]["required_reel_length_m"], 240)
			self.assertAlmostEqual(result["sheet"]["length_per_repeat_m"], .096)

	def test_flexo_repeat_count_is_order_qty_divided_by_ups(self):
		basis = bom_builder._flexo_repeat_basis({"reel_width_mm": 330}, {"ups": 4, "reel_area": 79.2}, 10001)
		self.assertEqual(basis["repeat_count"], 2500.25)
		self.assertAlmostEqual(basis["length_per_repeat_m"], 240 / 2500.25)
