import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api import offset_calculator
from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.savinda_quotation.savinda_quotation import SavindaQuotation


class TestQuotationMaterial(unittest.TestCase):
	def apply(self, rows, resolved="Customer Board"):
		with patch.object(frappe, "db", Mock()) as db, patch.object(
			offset_calculator, "resolve_common_material_name", return_value=resolved
		) as resolve:
			db.get_value.return_value = "BOARD-001"
			SavindaQuotation._apply_common_material(SimpleNamespace(items=rows))
			return db, resolve

	def test_user_material_is_preserved_without_lookup(self):
		row = frappe._dict(material="  Customer's chosen board  ", calculation_breakdown="CB-1")
		db, resolve = self.apply([row])
		self.assertEqual(row.material, "  Customer's chosen board  ")
		db.get_value.assert_not_called()
		resolve.assert_not_called()

	def test_blank_material_is_fetched_from_calculation(self):
		for blank in (None, "", "   "):
			with self.subTest(blank=blank):
				row = frappe._dict(material=blank, calculation_breakdown="CB-1")
				db, resolve = self.apply([row])
				self.assertEqual(row.material, "Customer Board")
				db.get_value.assert_called_once_with("Calculation Breakdown", "CB-1", "base_material")
				resolve.assert_called_once_with(base_material="BOARD-001", calculation_breakdown="CB-1")

	def test_unresolved_material_stays_blank(self):
		row = frappe._dict(material="", calculation_breakdown=None)
		db, _ = self.apply([row], resolved="")
		self.assertEqual(row.material, "")
		db.get_value.assert_not_called()

	def test_repeated_validation_keeps_fetched_material(self):
		row = frappe._dict(material="", calculation_breakdown="CB-1")
		self.apply([row])
		db, resolve = self.apply([row], resolved="Different Board")
		self.assertEqual(row.material, "Customer Board")
		db.get_value.assert_not_called()
		resolve.assert_not_called()
