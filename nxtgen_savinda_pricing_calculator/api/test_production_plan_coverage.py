import unittest
from types import SimpleNamespace

import frappe

from nxtgen_savinda_pricing_calculator.api.production_plan_coverage import order_coverage


class TestProductionPlanCoverage(unittest.TestCase):
	def order(self, same_item=False):
		return SimpleNamespace(name="SO", items=[
			frappe._dict(name="A", item_code="FG-A", qty=10, conversion_factor=1),
			frappe._dict(name="B", item_code="FG-A" if same_item else "FG-B", qty=20, conversion_factor=1)])

	def plan(self, name="PP", **values):
		return frappe._dict({"name": name, "docstatus": 0, "custom_sales_order": "SO", **values})

	def test_draft_reserves_item_without_double_counting_ticket_and_native_rows(self):
		pp = self.plan(custom_ticket_items=[frappe._dict(sales_order_item="A", qty=10)],
			po_items=[frappe._dict(sales_order_item="A", planned_qty=10)])
		result = order_coverage(self.order(), [pp])
		self.assertEqual(result["remaining"], {"B": 20})
		self.assertEqual(result["planned"]["A"], 10)
		self.assertEqual(result["existing_plans"], ["PP"])

	def test_partial_quantities_across_plans_leave_only_the_balance(self):
		plans = [self.plan(name="PP-1", custom_ticket_items=[frappe._dict(sales_order_item="A", qty=4)]),
			self.plan(name="PP-2", docstatus=1, custom_ticket_items=[frappe._dict(sales_order_item="A", qty=3)])]
		self.assertEqual(order_coverage(self.order(), plans)["remaining"], {"A": 3, "B": 20})

	def test_cancelled_plans_do_not_reserve_items(self):
		pp = self.plan(docstatus=2, custom_ticket_items=[frappe._dict(sales_order_item="A", qty=10)])
		self.assertEqual(order_coverage(self.order(), [pp])["remaining"], {"A": 10, "B": 20})

	def test_identical_fg_codes_are_distinguished_by_sales_order_row(self):
		pp = self.plan(custom_ticket_items=[frappe._dict(sales_order_item="A", fg_item="FG-A", qty=10)])
		self.assertEqual(order_coverage(self.order(same_item=True), [pp])["remaining"], {"B": 20})

	def test_legacy_tickets_allocate_by_item_and_do_not_duplicate_native_coverage(self):
		pp = self.plan(custom_ticket_items=[frappe._dict(fg_item="FG-A", qty=15)],
			po_items=[frappe._dict(sales_order_item="A", planned_qty=10)])
		self.assertEqual(order_coverage(self.order(same_item=True), [pp])["remaining"], {"B": 15})

	def test_stock_uom_quantities_convert_to_sales_uom(self):
		so = self.order()
		so.items[0].conversion_factor = 1000
		pp = self.plan(po_items=[frappe._dict(sales_order_item="A", planned_qty=5000)])
		self.assertEqual(order_coverage(so, [pp])["remaining"], {"A": 5, "B": 20})

	def test_native_combined_rows_use_references_without_inflating_first_item(self):
		pp = self.plan(po_items=[frappe._dict(sales_order_item="A", sales_order="", planned_qty=15)],
			prod_plan_references=[frappe._dict(sales_order_item="A", qty=5), frappe._dict(sales_order_item="B", qty=10)])
		self.assertEqual(order_coverage(self.order(), [pp])["remaining"], {"A": 5, "B": 10})

	def test_split_ticket_rows_sum_to_one_sales_order_item(self):
		pp = self.plan(custom_ticket_items=[frappe._dict(sales_order_item="A", qty=6),
			frappe._dict(sales_order_item="A", qty=4), frappe._dict(sales_order_item="B", qty=20)])
		self.assertEqual(order_coverage(self.order(), [pp])["remaining"], {})
