# Copyright (c) 2026, Techincglobal.com and Contributors
# See license.txt

import unittest
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.packing import packing


class TestPacking(unittest.TestCase):
	def setUp(self):
		self.pp = frappe._dict(name="PP-TEST", company="Test Company", custom_sales_order="SO-TEST", check_permission=Mock())
		self.pk = frappe._dict(name="PACK-TEST", job__npd_number="PP-TEST", item="FG-TEST", packed_qty=100,
			extra=5, docstatus=1, is_deliverd=0, check_permission=Mock())
		self.dn = frappe.get_doc({"doctype": "Delivery Note", "customer": "Test Customer", "company": "Test Company",
			"currency": "LKR", "conversion_rate": 1, "items": [{"item_code": "FG-TEST", "item_name": "Test FG",
				"qty": 1000, "rate": 50, "price_list_rate": 50, "amount": 50000, "base_amount": 50000,
				"uom": "Nos", "stock_uom": "Nos", "conversion_factor": 1, "warehouse": "FG Warehouse",
				"against_sales_order": "SO-TEST", "so_detail": "SO-LINE-1", "description": "Test FG"}]})

	def get_doc(self, doctype, name):
		return {"Production Plan": self.pp, "Packing": self.pk,
			"Sales Order": frappe._dict(check_permission=Mock())}[doctype]

	def fetch(self, names=None):
		with patch.object(frappe, "get_doc", side_effect=self.get_doc), patch.object(frappe, "has_permission", return_value=True), patch(
			"erpnext.selling.doctype.sales_order.sales_order.make_delivery_note", return_value=self.dn) as mapper:
			result = packing.get_delivery_items_from_packings(names or ["PACK-TEST"], production_plan="PP-TEST")
			mapper.assert_called_once_with("SO-TEST")
			return result

	def test_pp_picker_returns_paid_and_separate_zero_rate_extra_with_so_links(self):
		result = self.fetch()
		paid, extra = result["items"]
		self.assertEqual((paid["qty"], paid["rate"], extra["qty"], extra["rate"]), (100, 50, 5, 0))
		for row in (paid, extra):
			self.assertEqual(row["against_sales_order"], "SO-TEST")
			self.assertEqual(row["so_detail"], "SO-LINE-1")
			self.assertEqual(row["custom_packing"], "PACK-TEST")
			self.assertEqual(row["warehouse"], "FG Warehouse")
		self.assertEqual(extra["stock_qty"], 5)
		self.assertEqual(extra["base_amount"], 0)
		self.assertEqual(extra["price_list_rate"], 0)
		self.assertEqual(extra["discount_percentage"], 100)
		self.assertEqual(extra["is_free_item"], 1)

	def test_no_extra_creates_only_paid_line(self):
		self.pk.extra = 0
		self.assertEqual(len(self.fetch()["items"]), 1)

	def test_extra_only_packing_has_no_zero_quantity_paid_line(self):
		self.pk.packed_qty = 0
		rows = self.fetch()["items"]
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0]["qty"], rows[0]["rate"]), (5, 0))

	def test_duplicate_selection_is_not_delivered_twice(self):
		result = self.fetch(["PACK-TEST", "PACK-TEST"])
		self.assertEqual(result["packing_count"], 1)
		self.assertEqual(len(result["items"]), 2)

	def test_packing_from_different_pp_is_rejected(self):
		self.pk.job__npd_number = "PP-OTHER"
		with self.assertRaises(frappe.ValidationError):
			self.fetch()

	def test_delivered_or_unapproved_packing_is_rejected(self):
		for docstatus, delivered in [(0, 0), (1, 1), (2, 0)]:
			self.pk.docstatus, self.pk.is_deliverd = docstatus, delivered
			with self.assertRaises(frappe.ValidationError):
				self.fetch()

	def test_negative_extra_is_rejected(self):
		self.pk.extra = -1
		with self.assertRaises(frappe.ValidationError):
			self.fetch()

	def test_npd_plan_without_so_uses_standard_item_defaults(self):
		self.pp.custom_sales_order = None
		self.pp.custom_customer = "Test Customer"
		self.dn.set("items", [])
		def defaults():
			for row in self.dn.items:
				row.rate, row.uom, row.stock_uom, row.conversion_factor = 50, "Nos", "Nos", 1
				row.warehouse = "FG Warehouse"
		with patch.object(frappe, "get_doc", side_effect=self.get_doc), patch.object(frappe, "has_permission", return_value=True), patch.object(
			frappe, "new_doc", return_value=self.dn), patch.object(self.dn, "set_missing_values", side_effect=defaults):
			result = packing.get_delivery_items_from_packings(["PACK-TEST"], production_plan="PP-TEST")
		self.assertEqual(len(result["items"]), 2)
		self.assertTrue(all(not row.get("against_sales_order") and not row.get("so_detail") for row in result["items"]))
		self.assertEqual(result["customer"], "Test Customer")

	def test_packed_plus_extra_cannot_exceed_free_stock(self):
		doc = frappe.get_doc({"doctype": "Packing", "job__npd_number": "PP-TEST", "item": "FG-TEST", "packed_qty": 100, "extra": 5})
		with patch.object(packing, "get_packing_quantities", return_value={"order_qty":100,"delivered_qty":0,"new_qty":100}):
			with self.assertRaises(frappe.ValidationError):
				doc._sync_and_validate_qty()
		with patch.object(packing, "get_packing_quantities", return_value={"order_qty":100,"delivered_qty":0,"new_qty":105}):
			doc._sync_and_validate_qty()
		self.assertEqual(doc.total, 105)

	def test_listing_filters_exact_pp_and_submitted_undelivered_records(self):
		with patch.object(frappe, "get_doc", side_effect=self.get_doc), patch.object(frappe, "get_list", return_value=[]) as query:
			packing.get_available_packings(production_plan="PP-TEST")
		self.assertEqual(query.call_args.kwargs["filters"],
			{"job__npd_number": ["in", ["PP-TEST"]], "is_deliverd": 0, "docstatus": 1})
		self.assertEqual(query.call_args.kwargs["limit_page_length"], 0)

	def test_free_extra_rate_is_restored_after_repricing(self):
		paid, extra = self.dn.items[0], self.dn.append("items", dict(self.dn.items[0].as_dict(), name=None))
		paid.custom_packing = "PACK-TEST"
		extra.custom_packing, extra.is_free_item = "PACK-TEST", 1
		extra.rate, extra.price_list_rate, extra.margin_rate_or_amount = 999, 999, 20
		with patch.object(self.dn, "calculate_taxes_and_totals") as totals:
			packing.enforce_packing_extra_rates(self.dn, "validate")
		self.assertEqual(extra.rate, 0)
		self.assertEqual(extra.margin_rate_or_amount, 0)
		self.assertEqual(paid.rate, 50)
		totals.assert_called_once()

	def test_delivery_flag_updated_once_for_paid_and_extra_lines(self):
		self.dn.items[0].custom_packing = "PACK-TEST"
		self.dn.append("items", {"custom_packing": "PACK-TEST", "is_free_item": 1})
		for event, delivered in [("on_submit", 1), ("on_cancel", 0)]:
			with patch.object(frappe.db, "exists", return_value=True), patch.object(frappe.db, "set_value") as setter:
				packing.mark_packings_delivered(self.dn, event)
			setter.assert_called_once_with("Packing", "PACK-TEST", "is_deliverd", delivered)

	def test_availability_reserves_packed_and_extra_quantities(self):
		with patch.object(frappe.db, "get_value", return_value=frappe._dict(custom_ticket_type="Job", custom_sales_order="SO-TEST")), patch.object(
			packing, "_sum", side_effect=lambda dt, filters, field="qty": {("Sales Order Item", "qty"): 100,
				("Delivery Note Item", "qty"): 0, ("Bin", "actual_qty"): 200, ("Packing", "packed_qty"): 100,
				("Packing", "extra"): 5}[(dt, field)]):
			self.assertEqual(packing.get_packing_quantities("PP-TEST", "FG-TEST")["new_qty"], 95)
