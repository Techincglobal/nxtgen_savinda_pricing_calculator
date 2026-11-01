import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import production_plan as api
from nxtgen_savinda_pricing_calculator.api import production_plan_coverage as coverage


class Plan:
	def __init__(self):
		self.name = "PP-NEW"
		self.custom_ticket_items = []
		self.po_items = []
		self.sales_orders = []
		self.insert = Mock(return_value=self)
		self.get_items = Mock()

	def get(self, key):
		return getattr(self, key, None)

	def set(self, key, value):
		setattr(self, key, value)

	def append(self, key, value):
		if not self.get(key):
			self.set(key, [])
		row = frappe._dict(value)
		self.get(key).append(row)
		return row


class TestPPSalesOrderCreation(unittest.TestCase):
	def setUp(self):
		for key, value in (("db", Mock()), ("flags", frappe._dict(in_test=False))):
			if not hasattr(frappe.local, key):
				setattr(frappe.local, key, value)
				self.addCleanup(lambda key=key: delattr(frappe.local, key))
		self.so = Mock(items=[frappe._dict(name="A", item_code="FG-A", qty=10),
			frappe._dict(name="B", item_code="FG-B", qty=20)], docstatus=1)
		self.so.name = "SO"
		self.so.get.return_value = None
		self.pp = Plan()
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(patch.object(frappe.db, "sql", return_value=[]))
		self.stack.enter_context(patch.object(frappe, "get_doc", return_value=self.so))
		self.stack.enter_context(patch.object(frappe, "has_permission", return_value=True))
		self.new_doc = self.stack.enter_context(patch.object(frappe, "new_doc", return_value=self.pp))
		self.stack.enter_context(patch.object(api.jt_api, "_default_company", return_value="Company"))
		self.stack.enter_context(patch.object(api, "_fullname", return_value="User"))
		self.stack.enter_context(patch.object(api, "today", return_value="2026-11-01"))
		self.stack.enter_context(patch.object(api, "now", return_value="2026-11-01 10:00:00"))

	def test_already_covered_order_returns_existing_pp_without_creating(self):
		existing = frappe._dict(name="PP-OLD", docstatus=0, custom_sales_order="SO",
			custom_ticket_items=[frappe._dict(sales_order_item="A", qty=10), frappe._dict(sales_order_item="B", qty=20)])
		with patch.object(coverage, "existing_plans", return_value=[existing]):
			result = api.create_plan_from_sales_order("SO")
		self.assertTrue(result["already_planned"])
		self.assertEqual(result["production_plan"], "PP-OLD")
		self.new_doc.assert_not_called()

	def test_partial_order_creates_only_remaining_ticket_and_native_items(self):
		existing = frappe._dict(name="PP-OLD", docstatus=0, custom_sales_order="SO",
			custom_ticket_items=[frappe._dict(sales_order_item="A", qty=10)])
		line = {"fg_item": "FG-B", "qty": 20, "has_bom": 1, "sales_order": "SO", "sales_order_item": "B"}
		with patch.object(coverage, "existing_plans", return_value=[existing]), \
			patch.object(api, "_gather_ticket", return_value=("Job", "Offset", {"custom_sales_order": "SO"}, [line])) as gather, \
			patch.object(api, "_populate_po_items_from_so") as native:
			result = api.create_plan_from_sales_order("SO")
		gather.assert_called_once_with("Sales Order", "SO", sales_order_items={"B"}, quantities={"B": 20})
		native.assert_called_once_with(self.pp, "SO", quantities={"B": 20})
		self.assertEqual([row.fg_item for row in self.pp.custom_ticket_items], ["FG-B"])
		self.assertFalse(result["already_planned"])
		self.pp.insert.assert_called_once_with(ignore_permissions=True)

	def test_native_items_are_filtered_and_remaining_qty_uses_stock_conversion(self):
		self.so.items[1].conversion_factor = 2
		self.pp.po_items = [frappe._dict(sales_order_item="A", planned_qty=10),
			frappe._dict(sales_order_item="B", planned_qty=40)]
		api._populate_po_items_from_so(self.pp, "SO", quantities={"B": 7})
		self.assertEqual(len(self.pp.po_items), 1)
		self.assertEqual(self.pp.po_items[0].sales_order_item, "B")
		self.assertEqual(self.pp.po_items[0].planned_qty, 14)
		self.assertEqual(self.pp.combine_items, 0)

	def test_force_refresh_preserves_the_partial_plan_scope_and_quantity(self):
		self.pp.custom_ticket_items = [frappe._dict(sales_order="SO", sales_order_item="B", fg_item="FG-B", qty=7)]
		self.pp.custom_created_by = "User"
		line = {"fg_item": "FG-B", "qty": 7, "has_bom": 1, "sales_order": "SO", "sales_order_item": "B"}
		with patch.object(api, "_resolve_source", return_value=("Sales Order", "SO")), \
			patch.object(api, "_gather_ticket", return_value=("Job", "Offset", {}, [line])) as gather, \
			patch.object(api, "_build_po_items"):
			api._populate_pp_ticket_fields(self.pp, force=True)
		gather.assert_called_once_with("Sales Order", "SO", sales_order_items={"B"}, quantities={"B": 7})
		self.assertEqual([row.qty for row in self.pp.custom_ticket_items], [7])

	def test_partial_plan_artwork_comes_from_remaining_fg_and_quote_from_so(self):
		self.so.custom_savinda_quotation = "SQ-SO"
		self.so.get.side_effect = lambda key: "SQ-SO" if key == "custom_savinda_quotation" else None
		with patch.object(api.jt_api, "_fg_context", side_effect=lambda fg: {"product_library": "PL-" + fg}), \
			patch.object(api.jt_api, "_cb_fields", return_value={}), \
			patch.object(api.jt_api, "_pl", side_effect=lambda name: {"artwork_no": name + "-ART", "artwork_version": "V2", "quotation_no": "OLD-SQ", "department": "Flexo"}), \
			patch.object(api.jt_api, "_item_line_from_cb", return_value={}), \
			patch.object(api.jt_api, "_finishings_text", return_value=""), \
			patch.object(api, "_resolve_bom_no", return_value=None):
			_, pricing, header, rows = api._gather_ticket("Sales Order", "SO", sales_order_items={"B"}, quantities={"B": 7})
		self.assertEqual(pricing, "Flexo")
		self.assertEqual(header["custom_art_no"], "PL-FG-B-ART")
		self.assertEqual(header["custom_art_version"], "V2")
		self.assertEqual(header["custom_quote_no"], "SQ-SO")
		self.assertEqual([(r["sales_order_item"], r["qty"]) for r in rows], [("B", 7)])

	def test_standard_erpnext_quotation_links_are_supported(self):
		self.so.items[0].prevdoc_docname = "QTN-1"
		self.so.items[1].prevdoc_docname = "QTN-1"
		self.assertEqual(api._sales_order_quotation_ref(self.so), "QTN-1")
