import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils import jinja


class TestTicketGrouping(unittest.TestCase):
	def print_data(self, rows):
		doc = frappe._dict(name="TEST-PP", creation="2026-10-05", posting_date="2026-10-05",
			custom_pricing_type="Offset", custom_offset_planning=rows, custom_ticket_items=[], po_items=[], mr_items=[])
		db = Mock()
		db.get_value.return_value = None
		db.exists.return_value = False
		with ExitStack() as stack:
			stack.enter_context(patch.object(frappe, "get_doc", return_value=doc))
			stack.enter_context(patch.object(frappe, "db", new=db))
			stack.enter_context(patch.object(jinja, "format_date", side_effect=lambda value: value))
			stack.enter_context(patch("nxtgen_savinda_pricing_calculator.api.production_plan._ensure_planning", return_value=False))
			for helper, result in (("_fg_bom_calc", {}), ("_fg_pl", {}), ("_job_costing_cfg", {}), ("_company_logo", ""), ("_ticket_base_materials", [])):
				stack.enter_context(patch.object(jinja, helper, return_value=result))
			return jinja.get_ticket_print_data(doc.name)

	def test_grouped_assembly_parent_has_two_component_breakdowns(self):
		rows = [
			frappe._dict(fg_item="FG", item_name="Assembly", qty=2720, planning_type="Assembly", is_group=1),
			frappe._dict(fg_item="SFG-A", parent_item="FG", qty=2720, planning_type="Manufacture", is_group=1, full_sheet_qty=930, cuts=2, ups=4),
			# Children are grouped by parent link, even without their own is_group flag.
			frappe._dict(fg_item="SFG-B", parent_item="FG", qty=2720, planning_type="Manufacture", is_group=0, full_sheet_qty=1660, cuts=1, ups=2),
		]
		data = self.print_data(rows)
		self.assertEqual(len(data["lines"]), 1)
		parent = data["lines"][0]
		self.assertEqual(parent["item_code"], "FG")
		self.assertEqual([r["item_code"] for r in parent["line_item_bk"]], ["SFG-A", "SFG-B"])
		self.assertEqual(data["header"]["quantity"], 2720)
		self.assertIn("ups", parent)

	def test_ungrouped_assembly_is_still_excluded(self):
		data = self.print_data([
			frappe._dict(fg_item="FG", qty=10, planning_type="Assembly", is_group=0),
			frappe._dict(fg_item="SFG", qty=10, planning_type="Manufacture", is_group=0),
		])
		self.assertEqual([line["item_code"] for line in data["lines"]], ["SFG"])

	def test_tolerance_and_nested_assembly_not_in_print_breakdown(self):
		data = self.print_data([
			frappe._dict(fg_item="FG", qty=10, planning_type="Assembly", is_group=1),
			frappe._dict(fg_item="SFG", parent_item="FG", planning_type="Manufacture"),
			frappe._dict(fg_item="TOLERANCE", parent_item="FG", is_tolerance=1),
			frappe._dict(fg_item="ASSEMBLY", parent_item="FG", planning_type="Assembly"),
		])
		self.assertEqual([row["item_code"] for row in data["lines"][0]["line_item_bk"]], ["SFG"])
