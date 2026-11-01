import unittest
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import production_plan as api


class TestPPLibrarySpecifications(unittest.TestCase):
	def setUp(self):
		for key, value in (("db", Mock()), ("flags", frappe._dict(in_test=False))):
			if not hasattr(frappe.local, key):
				setattr(frappe.local, key, value)
				self.addCleanup(lambda key=key: delattr(frappe.local, key))

	def plan(self, **values):
		doc = frappe._dict(docstatus=0, custom_pricing_type="Flexo",
			custom_ticket_items=[frappe._dict(fg_item="FG")])
		doc.update(values)
		doc.set = lambda key, value: doc.__setitem__(key, value)
		return doc

	def test_machine_resolves_offset_master_to_workstation(self):
		def read(dt, *args, **kwargs):
			return {"printing_machine": "PRESS", "winding_direction": "OUT", "pcs_per_roll": 1000, "core_size": "76 mm"} if dt == "Product Library" else "WS"
		with patch.object(api.jt_api, "_pl_name_for_fg", return_value="PL"), \
			patch.object(frappe.db, "get_value", side_effect=read), \
			patch.object(frappe.db, "exists", side_effect=lambda dt, name: name if name == "WS" else None):
			values = api._pp_library_specifications("FG")
		self.assertEqual(values, {"custom_workstation": "WS", "custom_winding_direction": "OUT",
			"custom_pcs_per_roll": 1000, "custom_core_size": "76 mm"})

	def test_missing_machine_mapping_leaves_manual_workstation_selection(self):
		with patch.object(api.jt_api, "_pl_name_for_fg", return_value="PL"), \
			patch.object(frappe.db, "get_value", side_effect=[{"printing_machine": "UNMAPPED"}, None]), \
			patch.object(frappe.db, "exists", return_value=None):
			self.assertNotIn("custom_workstation", api._pp_library_specifications("FG"))

	def test_fills_blanks_preserving_manual_values_and_multiple_qty_tiers(self):
		doc = self.plan(custom_winding_direction="MANUAL", custom_workstation="USER-WS",
			custom_ticket_items=[frappe._dict(fg_item="FG"), frappe._dict(fg_item="FG")])
		with patch.object(api, "_pp_library_specifications", return_value={"custom_winding_direction": "OUT",
			"custom_workstation": "PL-WS", "custom_pcs_per_roll": 1000, "custom_core_size": "76 mm"}) as defaults:
			api._fill_pp_library_specifications(doc)
		defaults.assert_called_once_with("FG")
		self.assertEqual(doc.custom_winding_direction, "MANUAL")
		self.assertEqual(doc.custom_workstation, "USER-WS")
		self.assertEqual(doc.custom_pcs_per_roll, 1000)
		self.assertEqual(doc.custom_core_size, "76 mm")

	def test_offset_only_fetches_workstation(self):
		doc = self.plan(custom_pricing_type="Offset")
		with patch.object(api, "_pp_library_specifications", return_value={"custom_workstation": "WS",
			"custom_pcs_per_roll": 1000, "custom_core_size": "76 mm"}):
			api._fill_pp_library_specifications(doc)
		self.assertEqual(doc.custom_workstation, "WS")
		self.assertFalse(doc.get("custom_pcs_per_roll"))
		self.assertFalse(doc.get("custom_core_size"))

	def test_multiple_fgs_and_submitted_plans_do_not_auto_pick_a_library(self):
		with patch.object(api, "_pp_library_specifications") as defaults:
			api._fill_pp_library_specifications(self.plan(custom_ticket_items=[frappe._dict(fg_item="FG-A"), frappe._dict(fg_item="FG-B")]))
			api._fill_pp_library_specifications(self.plan(docstatus=1))
		defaults.assert_not_called()

	def test_missing_library_and_manual_po_items_supported(self):
		with patch.object(api.jt_api, "_pl_name_for_fg", return_value=None):
			self.assertEqual(api._pp_library_specifications("FG"), {})
		self.assertEqual(api._pp_specification_fgs(self.plan(custom_ticket_items=[],
			po_items=[frappe._dict(item_code="MANUAL-FG")])), ["MANUAL-FG"])

	def test_fetch_api_checks_item_and_library_read_permissions(self):
		item, library = Mock(), Mock()
		with patch.object(frappe, "get_doc", side_effect=[item, library]), \
			patch.object(api.jt_api, "_pl_name_for_fg", return_value="PL"), \
			patch.object(api, "_pp_library_specifications", return_value={}) as defaults:
			api.get_pp_library_specifications("FG")
		item.check_permission.assert_called_once_with("read")
		library.check_permission.assert_called_once_with("read")
		defaults.assert_called_once_with("FG")
