import unittest
from unittest.mock import patch

import frappe

from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils.jinja import _ticket_base_materials


class TestTicketBaseMaterials(unittest.TestCase):
	def test_unique_materials_preserve_planning_order(self):
		rows = [frappe._dict(base_material=code) for code in ("BOARD-B", "BOARD-A", "BOARD-B", "", None)]
		with patch.object(frappe, "get_all", return_value=[
			frappe._dict(name="BOARD-A", item_name="Ivory Board"),
			frappe._dict(name="BOARD-B", item_name="Duplex Board"),
		]) as get_all:
			self.assertEqual(_ticket_base_materials(rows), [
				{"item_code": "BOARD-B", "item_name": "Duplex Board"},
				{"item_code": "BOARD-A", "item_name": "Ivory Board"},
			])
			self.assertEqual(get_all.call_count, 1)

	def test_empty_planning_needs_no_item_query(self):
		with patch.object(frappe, "get_all") as get_all:
			self.assertEqual(_ticket_base_materials([]), [])
			self.assertEqual(_ticket_base_materials([frappe._dict(base_material="")]), [])
			get_all.assert_not_called()

	def test_missing_item_name_falls_back_to_code(self):
		with patch.object(frappe, "get_all", return_value=[]):
			self.assertEqual(_ticket_base_materials([frappe._dict(base_material="BOARD")]), [
				{"item_code": "BOARD", "item_name": "BOARD"},
			])
