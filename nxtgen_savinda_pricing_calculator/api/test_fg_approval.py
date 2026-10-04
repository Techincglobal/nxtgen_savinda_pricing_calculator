import unittest
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import fg_approval


class TestFGApproval(unittest.TestCase):
	def item(self, **values):
		doc = Mock()
		doc.get.side_effect = values.get
		doc.get_doc_before_save.return_value = None
		return doc

	def test_standard_raw_material_and_legacy_items_are_untouched(self):
		for values in ({}, {"custom_cost_item": "LEGACY"}, {"item_group": "Raw Material"}):
			fg_approval.validate_item_approval(self.item(**values))

	def test_approval_requirement_cannot_be_removed(self):
		doc = self.item(custom_fg_approval_required=0)
		doc.get_doc_before_save.return_value = frappe._dict(custom_fg_approval_required=1)
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			fg_approval.validate_item_approval(doc)

	def test_creation_marker_cannot_be_manually_assigned(self):
		doc = self.item(custom_fg_approval_required=1)
		doc.is_new.return_value = True
		with patch.object(fg_approval, "_authorized", return_value=False), patch.object(
			frappe, "throw", side_effect=frappe.ValidationError
		), self.assertRaises(frappe.ValidationError):
			fg_approval.validate_item_approval(doc)

	def test_product_library_approval_cannot_be_edited_directly(self):
		doc = self.item(approval_required=1, approval_status=fg_approval.APPROVED)
		doc.is_new.return_value = False
		doc.get_doc_before_save.return_value = frappe._dict(approval_required=1, approval_status=fg_approval.PENDING)
		with patch.object(fg_approval, "_authorized", return_value=False), patch.object(
			frappe, "throw", side_effect=frappe.ValidationError
		), self.assertRaises(frappe.ValidationError):
			fg_approval.validate_product_library(doc)

	def test_ordinary_product_library_save_does_not_notify(self):
		doc = self.item(approval_required=1)
		doc.approval_status = fg_approval.PENDING
		doc.get_doc_before_save.return_value = frappe._dict(approval_status=fg_approval.PENDING)
		with patch.object(frappe, "enqueue") as enqueue:
			fg_approval.on_product_library_update(doc)
			enqueue.assert_not_called()

	def test_all_cost_quantities_need_matching_positive_pricing(self):
		from nxtgen_savinda_pricing_calculator.api import pricing_rule
		doc = frappe._dict(cost_item="TEST-CI", fg_item="TEST-FG", customer="TEST-CUSTOMER")
		with patch.object(pricing_rule, "_tier_qtys_for_cost_item", return_value=[100, 500]), patch.object(
			pricing_rule, "_active_auto_rule", side_effect=[frappe._dict(rate=50), None]
		), patch.object(fg_approval, "today", return_value="2026-10-04"), patch.object(
			frappe, "throw", side_effect=frappe.ValidationError
		), self.assertRaises(frappe.ValidationError):
			fg_approval.check_pricing_for_approval(doc)
