import unittest
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api.offset_calculator import resolve_common_material_name


class TestOptionalMaterialFields(unittest.TestCase):
	def setUp(self):
		if not hasattr(frappe.local, "flags"):
			frappe.local.flags = frappe._dict(in_test=False)
			self.addCleanup(lambda: delattr(frappe.local, "flags"))

	def resolve(self, columns, values, **kwargs):
		with patch.object(frappe, "db", Mock()) as db:
			db.has_column.side_effect = lambda dt, field: (dt, field) in columns
			def get_value(dt, name, field):
				if (dt, field) in {("Item", "custom_board_and_paper_group"), ("Opportunity", "custom_board"), ("Boards and Papers", "common_name")}:
					self.assertIn((dt, field), columns, "Queried an uninstalled optional column")
				return values.get((dt, str(name), field))
			db.get_value.side_effect = get_value
			result = resolve_common_material_name(**kwargs)
			return result, db.get_value.call_args_list

	def test_cloud_missing_item_field_falls_back_to_inquiry_material(self):
		result, calls = self.resolve({("Boards and Papers", "item"), ("Boards and Papers", "common_name"), ("Opportunity", "custom_board")}, {
			("Item", "FLEXO-BOARD", "item_name"): "Flexo substrate",
			("Calculation Breakdown", "CB-1", "ref"): "OPP-1",
			("Opportunity", "OPP-1", "custom_board"): "Customer-facing substrate",
		}, base_material="FLEXO-BOARD", calculation_breakdown="CB-1")
		self.assertEqual(result, "Customer-facing substrate")
		self.assertFalse(any(c.args[2] == "custom_board_and_paper_group" for c in calls))

	def test_missing_optional_fields_and_mapping_return_blank_without_error(self):
		result, _ = self.resolve({("Boards and Papers", "item")}, {}, base_material="FLEXO-BOARD", calculation_breakdown="CB-1")
		self.assertEqual(result, "")

	def test_existing_item_group_mapping_is_preserved(self):
		result, _ = self.resolve({("Boards and Papers", "item"), ("Item", "custom_board_and_paper_group")}, {
			("Item", "BOARD-1", "custom_board_and_paper_group"): "Configured Board",
		}, base_material="BOARD-1")
		self.assertEqual(result, "Configured Board")

	def test_existing_direct_board_mapping_is_preserved(self):
		result, _ = self.resolve({("Boards and Papers", "item")}, {
			("Boards and Papers", "Known Board", "item"): "Existing mapping",
		}, raw_material="Known Board")
		self.assertEqual(result, "Existing mapping")
