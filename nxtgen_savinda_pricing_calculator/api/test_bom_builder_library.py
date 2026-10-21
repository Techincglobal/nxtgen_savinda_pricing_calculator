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

	def test_flexo_library_width_is_reel_width_and_length_is_label_length(self):
		values = self.inputs(self.library(width_mm=333, length_mm=80, full_sheet_size="31x43"), "Flexo")
		self.assertEqual(values, {"reel_width_mm":333,"product_length_mm":80})
		self.assertNotIn("product_width_mm", values)

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
