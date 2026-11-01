"""Builder submission order, exact child links and bottom-up cost propagation."""
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import bom_builder as api


class TestBOMBuilderSubmission(unittest.TestCase):
	def setUp(self):
		for key, value in (("db", Mock()), ("flags", frappe._dict(in_test=False))):
			if not hasattr(frappe.local, key):
				setattr(frappe.local, key, value)
				self.addCleanup(lambda key=key: delattr(frappe.local, key))
		self.docs, self.events = {}, []
		self.fail_item = None
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(patch.object(frappe, "get_doc", side_effect=self.get_doc))
		self.stack.enter_context(patch.object(frappe.db, "get_value", side_effect=self.get_value))
		self.stack.enter_context(patch.object(frappe.db, "exists", return_value=True))
		self.commit = self.stack.enter_context(patch.object(frappe.db, "commit"))
		self.stack.enter_context(patch.object(api, "_active_submitted_bom", return_value=None))
		self.stamp = self.stack.enter_context(patch.object(api, "_apply_bom_calc_fields"))
		self.enable_sfg = self.stack.enter_context(patch.object(api, "_enable_builder_sfg"))
		self.flexo = self.stack.enter_context(patch.object(api, "_builder_is_flexo", return_value=False))
		self.stack.enter_context(patch.object(api, "_validate_flexo_sfg"))
		self.stack.enter_context(patch.object(api, "_get_or_create_operation", side_effect=lambda value: value))
		self.stack.enter_context(patch.object(api, "_get_or_create_workstation", return_value="WS"))

	def get_value(self, doctype, name, field, **kwargs):
		return "Nos" if doctype == "Item" and field == "stock_uom" else None

	def get_doc(self, value, name=None):
		if not isinstance(value, dict):
			return self.docs[name]
		test = self

		class BOM:
			def __init__(self):
				self.__dict__.update(value)
				self.items, self.operations = [], []
				self.docstatus = 0
				self.total_cost = 0
				self.set_rate_of_sub_assembly_item_based_on_bom = 0

			def append(self, key, row):
				getattr(self, key).append(frappe._dict(row))

			def insert(self, **kwargs):
				self.name = "BOM-" + str(len(test.docs) + 1)
				for row in self.items:
					if row.get("bom_no"):
						assert test.docs[row.bom_no].docstatus == 1
				test.docs[self.name] = self
				test.events.append(("insert", self.item))

			def update_cost(self, **kwargs):
				assert kwargs == dict(update_parent=False, from_child_bom=True, update_hour_rate=True)
				assert self.set_rate_of_sub_assembly_item_based_on_bom == 1
				self.total_cost = sum(row.qty * (test.docs[row.bom_no].total_cost
					if row.get("bom_no") else 10) for row in self.items)
				test.events.append(("cost", self.item))

			def submit(self):
				if self.item == test.fail_item:
					raise frappe.ValidationError("Submission failed")
				self.docstatus = 1
				test.events.append(("submit", self.item))

		return BOM()

	def operations(self):
		return [dict(sfg_code="PRINT-SFG", spec_name="PRINT", input_item_code="PAPER", input_qty=100,
			output_qty=200, common_sfg_point=True),
			dict(sfg_code="CUT-SFG", spec_name="CUT", input_qty=200, output_qty=100)]

	def test_single_fg_submits_leaf_first_and_rolls_cost_to_fg(self):
		result = api.create_bom_chain("FG", 100, self.operations(), [])
		self.assertEqual([item for event, item in self.events if event == "submit"], ["PRINT-SFG", "CUT-SFG", "FG"])
		self.assertEqual([bom.total_cost for bom in self.docs.values()], [5, 10, 10])
		self.assertEqual(self.docs[result["fg_bom"]].items[0].bom_no, "BOM-2")
		self.assertEqual([call.args[0] for call in self.enable_sfg.call_args_list], ["PRINT-SFG", "CUT-SFG"])
		self.commit.assert_not_called()

	def test_output_fg_operation_submits_without_extra_fg_bom(self):
		ops = self.operations()
		ops[-1]["output_is_fg"] = True
		result = api.create_bom_chain("FG", 100, ops, [])
		self.assertEqual(len(self.docs), 2)
		self.assertEqual(self.docs[result["fg_bom"]].total_cost, 10)
		self.assertEqual([item for event, item in self.events if event == "submit"], ["PRINT-SFG", "FG"])

	def test_single_fg_passes_reviewed_details_to_the_fg_bom(self):
		drivers = {"cut_sheet_l": 15.5, "cut_sheet_w": 21.5}
		result = api.create_bom_chain("FG", 100, self.operations(), [],
			calculation_breakdown="SELECTED-CB", overrides=drivers)
		self.stamp.assert_called_once_with(result["fg_bom"], "FG", "SELECTED-CB", drivers, 100)

	def test_multi_fg_shared_child_submits_before_both_fg_tails(self):
		ops = self.operations()
		ops[-1]["output_is_fg"] = True
		drivers = {"product_length_mm": 80}
		result = api.create_multi_bom(["FG-A", "FG-B"], "CI", 100, ops, [],
			calculation_breakdown="SELECTED-CB", overrides=drivers)
		self.assertEqual([item for event, item in self.events if event == "submit"], ["CI-PRINT-SFG", "FG-A", "FG-B"])
		self.enable_sfg.assert_called_once_with("CI-PRINT-SFG")
		for name in result["fg_boms"]:
			self.assertEqual(self.docs[name].items[0].bom_no, "BOM-1")
			self.assertEqual(self.docs[name].total_cost, 10)
		self.assertEqual(len(self.stamp.call_args_list), 2)
		for call in self.stamp.call_args_list:
			self.assertEqual(call.args[2:], ("SELECTED-CB", drivers, 100))
		self.commit.assert_not_called()

	def test_failure_stops_before_parent_and_does_not_commit_child(self):
		self.fail_item = "CUT-SFG"
		with self.assertRaises(frappe.ValidationError):
			api.create_bom_chain("FG", 100, self.operations(), [])
		self.assertNotIn(("insert", "FG"), self.events)
		self.commit.assert_not_called()

	def test_explicit_draft_api_does_not_submit(self):
		api.create_bom_chain("FG", 100, self.operations(), [], submit=0)
		self.assertFalse(any(event == "submit" for event, item in self.events))
		self.assertTrue(all(b.docstatus == 0 for b in self.docs.values()))

	def flexo_operations(self):
		ops = self.operations()
		ops[0].update(input_qty=79.2, output_qty=240, input_uom="sqmt", output_uom="Meter")
		ops[1].update(input_qty=240, output_qty=240, input_uom="Meter", output_uom="Meter")
		return ops

	def test_flexo_fg_consumes_metres_and_sfg_consumes_square_metres(self):
		self.flexo.return_value = True
		result = api.create_bom_chain("FG", 10000, self.flexo_operations(), [])
		fg = self.docs[result["fg_bom"]]
		first = self.docs["BOM-1"]
		self.assertAlmostEqual(first.items[0].qty, .33)
		self.assertEqual(fg.items[0].qty, .024)
		self.assertAlmostEqual(first.items[0].qty * fg.items[0].qty * 10000, 79.2)

	def test_flexo_output_fg_marker_changes_repeats_to_finished_pieces(self):
		self.flexo.return_value = True
		ops = self.flexo_operations()
		ops[-1]["output_is_fg"] = True
		result = api.create_bom_chain("FG", 10000, ops, [])
		self.assertEqual(self.docs[result["fg_bom"]].items[0].qty, .024)
		self.assertEqual(len(self.docs), 2)

	def test_flexo_split_preserves_total_film_usage(self):
		self.flexo.return_value = True
		ops = self.flexo_operations()
		ops[-1].update(split_to_item_unit=True, output_qty=10000, output_uom="Nos")
		result = api.create_bom_chain("FG", 10000, ops, [])
		self.assertEqual(self.docs[result["fg_bom"]].items[0].qty, 1)
		self.assertEqual(self.docs["BOM-2"].items[0].qty, .024)
		self.assertAlmostEqual(self.docs["BOM-1"].items[0].qty * .024 * 10000, 79.2)

	def test_flexo_multi_fg_uses_shared_metres_per_finished_label(self):
		self.flexo.return_value = True
		ops = self.flexo_operations()
		ops[-1]["output_is_fg"] = True
		result = api.create_multi_bom(["FG-A", "FG-B"], "CI", 10000, ops, [])
		self.assertEqual(self.docs["BOM-1"].item, "CI-PRINT-MTR-SFG")
		for name in result["fg_boms"]:
			self.assertEqual(self.docs[name].items[0].qty, .024)

	def test_dependency_order_is_bottom_up_even_if_names_are_reversed(self):
		docs = {"TOP": Mock(item="FG", items=[frappe._dict(item_code="SFG")]),
			"BOTTOM": Mock(item="SFG", items=[frappe._dict(item_code="PAPER")])}
		self.assertEqual(api._topo_order_boms(["TOP", "BOTTOM"], docs), ["BOTTOM", "TOP"])
		docs["BOTTOM"].items = [frappe._dict(item_code="FG")]
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			api._topo_order_boms(["TOP", "BOTTOM"], docs)
