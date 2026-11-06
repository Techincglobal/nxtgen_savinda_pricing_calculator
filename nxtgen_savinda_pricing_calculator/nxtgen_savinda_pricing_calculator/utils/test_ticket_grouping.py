import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils import jinja


class TestTicketGrouping(unittest.TestCase):
	def print_data(self, rows, tickets=None, bom_values=None, library_values=None, **header):
		doc = frappe._dict(name="TEST-PP", creation="2026-10-05", posting_date="2026-10-05",
			custom_pricing_type="Offset", custom_offset_planning=rows, custom_ticket_items=tickets or [], po_items=[], mr_items=[])
		doc.update(header)
		db = Mock()
		db.get_value.return_value = None
		db.exists.return_value = False
		with ExitStack() as stack:
			stack.enter_context(patch.object(frappe, "get_doc", return_value=doc))
			stack.enter_context(patch.object(frappe, "db", new=db))
			stack.enter_context(patch.object(jinja, "format_date", side_effect=lambda value: value))
			stack.enter_context(patch("nxtgen_savinda_pricing_calculator.api.production_plan._ensure_planning", return_value=False))
			for helper, result in (("_fg_bom_calc", bom_values or {}), ("_fg_pl", library_values or {}), ("_job_costing_cfg", {}), ("_company_logo", ""), ("_ticket_base_materials", [])):
				stack.enter_context(patch.object(jinja, helper, return_value=result))
			return jinja.get_ticket_print_data(doc.name)

	def test_pp_manual_flexo_specifications_are_used_in_print_header(self):
		data = self.print_data([], custom_pricing_type="Flexo", custom_workstation="PRESS-WS",
			custom_printing_machine="OLD MACHINE", custom_winding_direction="OUT",
			custom_pcs_per_roll=2000, custom_core_size="76 mm")
		self.assertEqual(data["header"]["printing_machine"], "PRESS-WS")
		self.assertEqual(data["header"]["winding_direction"], "OUT")
		self.assertEqual(data["header"]["pcs_per_roll"], 2000)
		self.assertEqual(data["header"]["core_size"], "76 mm")

	def test_expiry_date_is_separate_from_expected_date_on_planned_rows(self):
		for grouped in (0, 1):
			with self.subTest(grouped=grouped):
				data = self.print_data([frappe._dict(fg_item="FG", qty=10, is_group=grouped)],
					[frappe._dict(fg_item="FG", exp_date="2026-11-01", expiry_date="DEC 2028", size="150 x 200")])
				line = data["lines"][0]
				self.assertEqual(line["exp_date"], "2026-11-01")
				self.assertEqual(line["expiry_date"], "DEC 2028")

	def test_ticket_only_expiry_is_not_filled_from_expected_date(self):
		data = self.print_data([], [frappe._dict(fg_item="FG", qty=10, exp_date="2026-11-01")])
		self.assertEqual(data["lines"][0]["expiry_date"], "")
		self.assertEqual(data["lines"][0]["exp_date"], "2026-11-01")

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

	def test_planning_values_win_over_bom_and_library_for_both_job_types(self):
		for pricing in ("Offset", "Flexo"):
			rows = [frappe._dict(fg_item="FG", qty=100, item_name="Reviewed description",
				product_code="PLAN", size="Plan size", reel_length=600, reel_width=250,
				reel_area=150, full_sheet_qty=0, cut_sheet_qty=0, wastage=0, ups=4, cuts=2),
				frappe._dict(fg_item="FG", qty=50, reel_length=300, reel_width=250, reel_area=75)]
			kwargs = {"custom_pricing_type": pricing}
			if pricing == "Flexo":
				kwargs["custom_flexo_planning"] = rows
			data = self.print_data(rows, bom_values={"custom_reel_length_m": 999,
				"custom_reel_width_mm": 900, "custom_carton_size": "BOM size",
				"custom_no_of_ups": 20, "custom_no_of_cuts": 2},
				library_values={"customer_product_code": "LIBRARY", "product_size": "Library size"}, **kwargs)
			first, second = data["lines"]
			self.assertEqual((first["reel_length"], second["reel_length"]), (600, 300))
			self.assertEqual((first["size"], first["product_code"]), ("Plan size", "PLAN"))
			self.assertEqual((first["full_sheets"], first["cut_sheets"], first["wastage"]), (0, 0, 0))
			self.assertEqual(second["product_code"], "LIBRARY")
			self.assertNotIn("_planning_row", first)
