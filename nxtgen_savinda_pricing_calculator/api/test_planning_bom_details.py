import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import planning_bom_details as api, production_plan


class TestPlanningBOMDetails(unittest.TestCase):
	def setUp(self):
		for key, value in (("db", Mock()), ("flags", frappe._dict(in_test=False))):
			if not hasattr(frappe.local, key):
				setattr(frappe.local, key, value)
				self.addCleanup(lambda key=key: delattr(frappe.local, key))
		self.bom = frappe._dict(name="BOM-NEW", item="FG", docstatus=1, is_active=1,
			custom_calculation_breakdown="CB-NEW", custom_pricing_type="Offset",
			custom_no_of_ups=8, custom_no_of_cuts=2, custom_cut_sheet_ups=4,
			custom_full_sheet_size="31 x 43", custom_cut_sheet_size="15.5 x 21.5",
			items=[frappe._dict(item_code="SFG", bom_no="BOM-CHILD")])
		self.child = frappe._dict(name="BOM-CHILD", items=[frappe._dict(item_code="NEW-PAPER")])
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(patch.object(frappe, "get_doc", side_effect=lambda dt, name: self.bom if name == "BOM-NEW" else self.child))
		self.stack.enter_context(patch.object(frappe, "throw", side_effect=frappe.ValidationError))
		self.stack.enter_context(patch.object(frappe.db, "get_value", return_value="FG Name"))
		self.stack.enter_context(patch.object(frappe.db, "exists", return_value=True))
		self.stack.enter_context(patch.object(api.job_ticket, "_fg_context", return_value={"cost_item": "CI", "calculation_breakdown": "OLD-CB"}))
		self.cb = self.stack.enter_context(patch.object(api.job_ticket, "_cb_fields", return_value={
			"base_material": "OLD-PAPER", "ui_state": json.dumps({"form": {"no_of_ups": 2, "no_of_cuts": 1}})}))
		self.stack.enter_context(patch.object(api, "_library_bom_inputs", return_value={"no_of_ups": 4, "full_sheet_l": 40, "full_sheet_w": 50}))
		self.stack.enter_context(patch.object(api, "_get_config", return_value={
			"offset_wastage_pct": .05, "offset_wastage_min": 100, "flexo_wastage": [(99, 20, .1)]}))

	def test_selected_offset_bom_overrides_library_and_old_costing(self):
		values = api.planning_row_details("FG", "BOM-NEW", 10000)
		self.cb.assert_called_once_with("CB-NEW")
		self.assertEqual((values["ups"], values["cuts"]), (8, 2))
		self.assertEqual((values["cut_sheet_qty"], values["wastage"], values["full_sheet_qty"]), (2500, 125, 1313))
		self.assertEqual(values["cut_sheet_size"], "15.5 x 21.5")
		self.assertEqual(values["full_sheet_size"], "31 x 43")
		self.assertEqual(values["base_material"], "NEW-PAPER")
		self.assertNotIn("exp_date", values)
		self.assertNotIn("batch_no", values)

	def test_quantity_change_recalculates_sheet_counts(self):
		values = api.planning_row_details("FG", "BOM-NEW", 5000)
		self.assertEqual((values["cut_sheet_qty"], values["wastage"], values["full_sheet_qty"]), (1250, 100, 675))

	def test_flexo_selected_bom_controls_reel_geometry_and_fixed_setup(self):
		self.bom.update(custom_pricing_type="Flexo", custom_reel_width_mm=330, custom_reel_length_m=200,
			custom_required_reel_length_m=240, custom_base_item_qty=10000, custom_no_of_ups=4,
			custom_no_of_colors=3, custom_carton_size="70 x 80")
		values = api.planning_row_details("FG", "BOM-NEW", 10000, "Flexo")
		self.assertEqual((values["ups"], values["reel_width"], values["reel_length"]), (4, 330, 200))
		self.assertAlmostEqual(values["reel_area"], 79.2)
		self.assertEqual(values["size"], "70 x 80")
		halved = api.planning_row_details("FG", "BOM-NEW", 5000, "Flexo")
		self.assertEqual(halved["reel_length"], 100)
		self.assertAlmostEqual(halved["reel_area"], 42.9)
		zero = api.planning_row_details("FG", "BOM-NEW", 0, "Flexo")
		self.assertEqual(zero["reel_area"], 0)

	def test_flexo_minimum_stays_fixed_when_planning_qty_changes(self):
		self.bom.update(custom_pricing_type="Flexo", custom_reel_width_mm=250,
			custom_reel_length_m=1000, custom_required_reel_length_m=1250,
			custom_base_item_qty=10000, custom_no_of_ups=4, custom_no_of_colors=4)
		with patch.object(api, "_get_config", return_value={"flexo_wastage": [(99, 150, .09)],
			"flexo_minimum_wastage": {99: 250}}):
			for qty, area in ((5000, 187.5), (10000, 312.5), (20000, 582.5), (100000, 2762.5)):
				with self.subTest(qty=qty):
					self.assertAlmostEqual(api.planning_row_details("FG", "BOM-NEW", qty, "Flexo")["reel_area"], area)

	def test_wrong_item_inactive_and_draft_boms_are_rejected(self):
		for field, value in (("item", "OTHER-FG"), ("is_active", 0), ("docstatus", 0)):
			original = self.bom[field]
			self.bom[field] = value
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				api.planning_row_details("FG", "BOM-NEW", 1000)
			self.bom[field] = original

	def test_legacy_bom_without_snapshot_keeps_library_costing_fallback(self):
		values = api.planning_row_details("FG", None, 1000)
		self.assertEqual(values["calculation_breakdown"], "OLD-CB")
		self.assertEqual(values["ups"], 4)
		self.assertEqual(values["base_material"], "OLD-PAPER")

	def test_base_material_can_have_its_own_bom_without_being_exploded_further(self):
		self.child["items"] = [frappe._dict(item_code="OLD-PAPER", bom_no="RAW-MATERIAL-BOM")]
		values = api.planning_row_details("FG", "BOM-NEW", 1000)
		self.assertEqual(values["base_material"], "OLD-PAPER")
		self.bom.custom_base_material = "REVIEWED-FILM"
		self.assertEqual(api.planning_row_details("FG", "BOM-NEW", 1000)["base_material"], "REVIEWED-FILM")

	def test_save_updates_matching_native_bom_reference_and_preserves_dates_qty(self):
		row = frappe._dict(fg_item="FG", bom_no="BOM-NEW", qty=10000, exp_date="2026-12-01", batch_no="BATCH")
		row.set = lambda key, value: row.__setitem__(key, value)
		native = frappe._dict(item_code="FG", bom_no="OLD-BOM", planned_qty=10000, sales_order_item="SO-ROW")
		ticket = frappe._dict(fg_item="FG", bom_no="OLD-BOM", qty=10000)
		doc = frappe._dict(docstatus=0, custom_pricing_type="Offset", custom_offset_planning=[row],
			po_items=[native], custom_ticket_items=[ticket])
		production_plan._refresh_manual_planning_rows(doc)
		self.assertEqual(row.ups, 8)
		self.assertEqual(native.bom_no, "BOM-NEW")
		self.assertEqual(ticket.bom_no, "BOM-NEW")
		self.assertEqual((row.exp_date, row.batch_no, native.planned_qty, native.sales_order_item),
			("2026-12-01", "BATCH", 10000, "SO-ROW"))
