import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe
from frappe.model import workflow

from nxtgen_savinda_pricing_calculator.api import bom_builder as api


class TestBuilderSFGEnable(unittest.TestCase):
	def setUp(self):
		self.item = frappe._dict(name="PRINT-SFG", item_group="WIP", disabled=1,
			is_stock_item=1, is_sales_item=0, is_purchase_item=0, custom_fg_approval_required=0,
			check_permission=Mock(), save=Mock(), reload=Mock())
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(patch.object(frappe, "get_doc", return_value=self.item))
		self.stack.enter_context(patch.object(frappe, "session", frappe._dict(user="MANAGER")))
		self.roles = self.stack.enter_context(patch.object(frappe, "get_roles", return_value=["Item Manager"]))
		self.stack.enter_context(patch.object(frappe, "throw", side_effect=frappe.ValidationError))
		self.workflow_name = self.stack.enter_context(patch.object(workflow, "get_workflow_name", return_value="Item Creation Verification Workflow"))
		self.transitions = self.stack.enter_context(patch.object(workflow, "get_transitions", return_value=[frappe._dict(action="Enable")]))
		self.apply = self.stack.enter_context(patch.object(workflow, "apply_workflow", side_effect=self.enable))

	def enable(self, item, action):
		item.disabled = 0
		item.workflow_state = "Enabled"

	def test_runs_existing_enable_action_as_current_manager(self):
		api._enable_builder_sfg("PRINT-SFG")
		self.apply.assert_called_once_with(self.item, "Enable")
		self.item.check_permission.assert_called_once_with("write")
		self.item.save.assert_not_called()
		self.assertEqual(self.item.workflow_state, "Enabled")

	def test_enabled_sfg_is_unchanged(self):
		self.item.disabled = 0
		api._enable_builder_sfg("PRINT-SFG")
		self.apply.assert_not_called()
		self.item.save.assert_not_called()

	def test_without_workflow_uses_normal_save(self):
		self.workflow_name.return_value = None
		api._enable_builder_sfg("PRINT-SFG")
		self.apply.assert_not_called()
		self.item.save.assert_called_once_with()
		self.assertEqual(self.item.disabled, 0)

	def test_does_not_enable_fg_purchase_or_nonstock_items(self):
		for key in ("is_sales_item", "is_purchase_item", "custom_fg_approval_required", "is_stock_item"):
			original = self.item[key]
			self.item[key] = 0 if key == "is_stock_item" else 1
			with self.subTest(key=key), self.assertRaises(frappe.ValidationError):
				api._enable_builder_sfg("PRINT-SFG")
			self.item[key] = original
		self.apply.assert_not_called()

	def test_manager_role_is_required(self):
		self.roles.return_value = ["Manufacturing User"]
		with self.assertRaises(frappe.ValidationError):
			api._enable_builder_sfg("PRINT-SFG")
		self.apply.assert_not_called()

	def test_supply_chain_workflow_route_cannot_be_bypassed(self):
		self.item.item_group = "All SFG"
		self.transitions.return_value = [frappe._dict(action="Send for Supply Chain Verification")]
		with self.assertRaises(frappe.ValidationError):
			api._enable_builder_sfg("PRINT-SFG")
		self.apply.assert_not_called()
		self.assertEqual(self.item.disabled, 1)

	def test_workflow_rejection_stops_bom_build(self):
		self.apply.side_effect = frappe.ValidationError("Self approval is not allowed")
		with self.assertRaises(frappe.ValidationError):
			api._enable_builder_sfg("PRINT-SFG")
		self.assertEqual(self.item.disabled, 1)

	def test_flexo_sfg_stock_uom_must_match_metres_without_rewriting_item(self):
		with patch.object(frappe, "db", Mock(get_value=Mock(return_value="Meter"))):
			api._validate_flexo_sfg("PRINT-SFG", "Meter")
		with patch.object(frappe, "db", Mock(get_value=Mock(return_value="Nos"))), self.assertRaises(frappe.ValidationError):
			api._validate_flexo_sfg("OLD-REPEAT-SFG", "Meter")
		self.item.save.assert_not_called()

	def test_flexo_pieces_allowed_at_split_or_final_fg(self):
		api._validate_flexo_flow([{"output_uom": "Meter"}, {"output_uom": "Nos", "output_is_fg": True}])
		api._validate_flexo_flow([{"output_uom": "Meter"},
			{"output_uom": "Nos", "split_to_item_unit": True}, {"output_uom": "Nos"}])
		with self.assertRaises(frappe.ValidationError):
			api._validate_flexo_flow([{"output_uom": "Nos"}])
		with self.assertRaises(frappe.ValidationError):
			api._validate_flexo_flow([{"output_uom": "Nos", "split_to_item_unit": True}, {"output_uom": "Meter"}])
