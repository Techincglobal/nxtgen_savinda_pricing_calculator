import unittest
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import production_plan as api


class TestPPRequiredDate(unittest.TestCase):
	def row(self, **values):
		row = frappe._dict(values)
		row.set = lambda key, value: row.__setitem__(key, value)
		return row

	def plan(self, **values):
		doc = self.row(custom_ticket_type="Job", workflow_state="Draft", custom_req_date=None)
		doc.update(values)
		doc.get_doc_before_save = lambda: frappe._dict(workflow_state="Draft")
		return doc

	def test_sales_order_delivery_date_is_not_copied_to_pp_required_date(self):
		so = Mock(items=[], customer="CUSTOMER", customer_name="Customer",
			po_no="PO", delivery_date="2026-12-10")
		so.name = "SO"
		so.get.return_value = None
		with patch.object(frappe, "get_doc", return_value=so), \
			patch.object(api, "_first_fg_context_from_so", return_value=({}, {}, {}, "Offset")):
			_, _, header, _ = api._gather_ticket("Sales Order", "SO")
		self.assertNotIn("custom_req_date", header)
		self.assertEqual(so.delivery_date, "2026-12-10")

	def test_send_for_bom_allows_blank_date_but_final_approval_requires_it(self):
		api._sync_pp_required_date(self.plan())
		api._sync_pp_required_date(self.plan(workflow_state="BOM Validation"))
		api._sync_pp_required_date(self.plan(workflow_state="Supply Chain Validation"))
		api._sync_pp_required_date(self.plan(workflow_state="Approved", docstatus=0))
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			api._sync_pp_required_date(self.plan(workflow_state="Submitted"))

	def test_submission_requires_date_with_custom_workflow_state(self):
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			api._sync_pp_required_date(self.plan(workflow_state="Complete", docstatus=1))

	def test_required_date_updates_all_expected_dates_but_never_expiry_dates(self):
		for pricing in ("Offset", "Flexo"):
			with self.subTest(pricing=pricing):
				row = self.row(fg_item="FG", exp_date="2026-12-01", expiry_date="DEC 2028")
				offset = self.row(fg_item="FG", exp_date=None, expiry_date="DEC 2029")
				flexo = self.row(fg_item="FG", exp_date=None)
				po = self.row(item_code="FG", custom_exp_date=None, custom_expiry_date="DEC 2030")
				doc = self.plan(workflow_state="BOM Validation", custom_pricing_type=pricing,
					custom_req_date="2026-12-20", custom_ticket_items=[row],
					custom_offset_planning=[offset], custom_flexo_planning=[flexo], po_items=[po])
				api._sync_pp_required_date(doc)
				for line in (row, offset, flexo):
					self.assertEqual(line.exp_date, "2026-12-20")
				self.assertEqual(po.custom_exp_date, "2026-12-20")
				self.assertEqual(row.expiry_date, "DEC 2028")
				self.assertEqual(offset.expiry_date, "DEC 2029")
				self.assertEqual(po.custom_expiry_date, "DEC 2030")

	def test_save_hook_does_not_swallow_final_approval_date_validation(self):
		with patch.object(api, "_populate_pp_ticket_fields"), \
			patch.object(api, "_refresh_manual_planning_rows") as refresh, \
			patch.object(frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			api.on_production_plan_before_save(self.plan(workflow_state="Submitted"))
		refresh.assert_not_called()

	def test_submit_requires_header_date_even_if_old_ticket_rows_have_dates(self):
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			api.validate_ticket_item_exp_dates(self.plan(custom_ticket_items=[self.row(fg_item="FG", exp_date="2026-12-01")]))

	def test_standard_pp_is_unaffected(self):
		doc = self.plan(custom_ticket_type=None, workflow_state="BOM Validation")
		api._sync_pp_required_date(doc)
		api.validate_ticket_item_exp_dates(doc)
