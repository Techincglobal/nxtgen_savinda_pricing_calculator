import unittest
from unittest.mock import patch

import frappe

from nxtgen_savinda_pricing_calculator.api import bom_builder


class TestBOMBuilderMaterials(unittest.TestCase):
	def test_uoms_come_from_permission_aware_master_list(self):
		with patch.object(frappe, "get_list", return_value=["Kg", "Nos"]) as query:
			self.assertEqual(bom_builder.get_uoms(), ["Kg", "Nos"])
		query.assert_called_once_with("UOM", pluck="name", order_by="name asc", limit_page_length=0)

	def test_selected_uom_preserves_quantity_and_converts_stock_quantity(self):
		with patch.object(frappe.db, "exists", return_value=True), patch.object(
			bom_builder, "_to_stock_uom", return_value=(1000, "Gram", True)
		) as convert:
			row = bom_builder._bom_material_row({"item_code": "INK", "uom": "Kg"}, 2.5)
		convert.assert_called_once_with("INK", 1, "Kg")
		self.assertEqual(row["qty"], 2.5)
		self.assertEqual(row["uom"], "Kg")
		self.assertEqual(row["stock_uom"], "Gram")
		self.assertEqual(row["conversion_factor"], 1000)
		self.assertEqual(row["stock_qty"], 2500)

	def test_invalid_uom_is_rejected(self):
		with patch.object(frappe.db, "exists", return_value=False), patch.object(
			frappe, "throw", side_effect=frappe.ValidationError
		), self.assertRaises(frappe.ValidationError):
			bom_builder._bom_material_row({"item_code": "INK", "uom": "INVALID"}, 2)

	def test_missing_conversion_is_rejected(self):
		with patch.object(frappe.db, "exists", return_value=True), patch.object(
			bom_builder, "_to_stock_uom", return_value=(1, "Gram", False)
		), patch.object(frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(
			frappe.ValidationError
		):
			bom_builder._bom_material_row({"item_code": "INK", "uom": "Box"}, 2)
