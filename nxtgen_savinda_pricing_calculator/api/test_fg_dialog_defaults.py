import json
import unittest
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import manufacturing, production_plan as api


class TestFGDialogDefaults(unittest.TestCase):
	def setUp(self):
		if not hasattr(frappe.local, "db"):
			frappe.local.db = Mock()
			self.addCleanup(lambda: delattr(frappe.local, "db"))
		if not hasattr(frappe.local, "flags"):
			frappe.local.flags = frappe._dict(in_test=False)
			self.addCleanup(lambda: delattr(frappe.local, "flags"))

	def test_selected_line_wins_over_cost_item(self):
		with patch.object(frappe.db, "get_value", return_value={"pcs_per_role": 500, "winding_direction": "IN"}):
			result = api._packing_pl_defaults({"pcs_per_role": 1000, "winding_direction": "OUT"}, cost_item="CI")
		self.assertEqual(result, {"pl_pcs_per_roll": 1000, "pl_winding_direction": "OUT"})

	def test_flexo_fg_preview_uses_product_length_not_reel_length(self):
		state = {"form": {"product_length_mm": 80, "reel_length_m": 1124},
			"calc_result": {"sheet": {"reel_length": 1124}}}
		with patch.object(frappe.db, "exists", return_value=True), \
			patch.object(frappe.db, "get_value", return_value={"ui_state": json.dumps(state)}):
			fields = api.jt_api._cb_fields("CB")
		self.assertEqual(fields["_product_length_mm"], 80)
		self.assertEqual(fields["_reel_length"], 1124)
		fields["pricing_type"] = "Flexo"
		def get_value(doctype, *args, **kwargs):
			if doctype == "Cost Item Calculation":
				return "CB"
			if doctype == "cost Item":
				return {"cost_item_name": "Label"}
			return ""
		with patch.object(frappe.db, "get_value", side_effect=get_value), \
			patch.object(api.jt_api, "_cb_fields", return_value=fields), \
			patch.object(api, "_finishing_defaults_from_cb", return_value=[]), \
			patch.object(api, "_foil_defaults_from_cb", return_value={}), \
			patch.object(api, "_dept_for_pricing", return_value="Flexo"):
			self.assertEqual(api._pl_defaults_from_cost_item("CI")["pl_length_mm"], 80)
			self.assertEqual(api._cost_sheet_fg_defaults("CI", "CS", "FG")["pl_length_mm"], 80)

	def test_each_missing_value_falls_back_independently(self):
		with patch.object(frappe.db, "get_value", return_value={"pcs_per_role": 500, "winding_direction": "IN"}):
			result = api._packing_pl_defaults({"pcs_per_role": 0, "winding_direction": "OUT"}, cost_item="CI")
		self.assertEqual(result, {"pl_pcs_per_roll": 500, "pl_winding_direction": "OUT"})
		self.assertEqual(api._packing_pl_defaults(), {"pl_pcs_per_roll": 0, "pl_winding_direction": ""})

	def test_quotation_reads_only_its_selected_row(self):
		def get_value(doctype, name, fields, **kwargs):
			if doctype == "Savinda Quotation":
				return {"customer": "CUSTOMER"}
			if doctype == "Savinda Quotation Item":
				self.assertEqual(name, {"name": "ROW-2", "parent": "SQ", "parenttype": "Savinda Quotation"})
				return {"pcs_per_role": 2000, "winding_direction": "OUT"}
			if doctype == "cost Item":
				return {"pcs_per_role": 500, "winding_direction": "IN"}
		with patch.object(frappe.db, "exists", return_value=True), \
			patch.object(frappe.db, "get_value", side_effect=get_value), \
			patch.object(frappe, "get_doc", return_value=Mock()), \
			patch.object(api, "_pl_defaults_from_cost_item", return_value={"is_flexo": True}), \
			patch.object(api, "_pl_field_schema", return_value=[]):
			result = api.get_cost_item_fg_defaults("CI", "SQ", "ROW-2")
		self.assertEqual(result["defaults"]["pl_pcs_per_roll"], 2000)
		self.assertEqual(result["defaults"]["pl_winding_direction"], "OUT")

	def test_both_fg_creation_apis_reject_missing_artwork_before_inserting(self):
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError), \
			patch.object(frappe, "get_doc") as insert:
			for overrides in (None, {}, {"artwork_no": "  "}):
				with self.subTest(overrides=overrides), self.assertRaises(frappe.ValidationError):
					manufacturing.create_fg_item("FG", "FG", "Finished Goods", "Flexo", pl_overrides=overrides)
				with self.assertRaises(frappe.ValidationError):
					manufacturing.create_fg_variants("FG", "FG", "Finished Goods", "Flexo", variants=[{"value": "S"}], pl_overrides=overrides)
			insert.assert_not_called()

	def test_variants_store_independent_artwork_without_main_artwork(self):
		from nxtgen_savinda_pricing_calculator.api import fg_approval
		for shared in ({"quotation_no": "SQ-TEST"}, {"artwork_no": "MAIN", "quotation_no": "SQ-TEST"}):
			with self.subTest(shared=shared), \
				patch.object(frappe.db, "get_value", return_value=""), \
				patch.object(frappe.db, "has_column", return_value=False), \
				patch.object(frappe, "get_doc", side_effect=lambda data: frappe._dict(data, name=data["item_name"])), \
				patch.object(fg_approval, "insert_pending_fg") as insert:
				result = manufacturing.create_fg_variants("FG", "FG", "Finished Goods", "Flexo",
					variants=[{"value": "S", "artwork_no": " ART-S "}, {"value": "L", "artwork_no": "ART-L"}],
					pl_overrides=shared)
				self.assertEqual(len(result["items"]), 2)
				self.assertEqual([call.args[3]["artwork_no"] for call in insert.call_args_list], ["ART-S", "ART-L"])
				self.assertEqual([call.args[3]["quotation_no"] for call in insert.call_args_list], ["SQ-TEST", "SQ-TEST"])

	def test_missing_second_variant_artwork_rejects_all_even_with_shared_artwork(self):
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError), \
			patch.object(frappe, "get_doc") as insert, self.assertRaises(frappe.ValidationError):
			manufacturing.create_fg_variants("FG", "FG", "Finished Goods", "Flexo",
				variants=[{"value": "S", "artwork_no": "S-ART"}, {"value": "L", "artwork_no": " "}],
				pl_overrides={"artwork_no": "MAIN"})
		insert.assert_not_called()

	def test_new_fg_artwork_is_approved_without_approving_the_fg(self):
		from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils import jinja
		for required, artwork, approved in ((True, "ART-S", True), (True, " ", False), (False, "ART-S", False)):
			with self.subTest(required=required, artwork=artwork), \
				patch.object(frappe.db, "exists", return_value=True), \
				patch.object(frappe.db, "get_value", side_effect=lambda dt, *args, **kwargs: None if dt == "Product Library" else {"item_name": "FG"}), \
				patch.object(frappe.db, "has_column", return_value=False), \
				patch.object(frappe, "get_meta", return_value=Mock()), \
				patch.object(frappe, "get_doc", return_value=Mock(name="PL")) as create, \
				patch.object(frappe, "session", frappe._dict(user="CREATOR")), \
				patch.object(manufacturing, "now", return_value="2026-11-01 10:00:00"), \
				patch.object(manufacturing, "_foil_summary_from_cb", return_value={}), \
				patch.object(jinja, "get_cb_finishings", return_value=[]):
				manufacturing._upsert_product_library("FG", overrides={"artwork_no": artwork}, require_approval=required)
				data = create.call_args.args[0]
				self.assertEqual(data.get("artwork_validation_status") == "Approved", approved)
				if approved:
					self.assertEqual(data["artwork_validated_by"], "CREATOR")
					self.assertEqual(data["artwork_validated_on"], "2026-11-01 10:00:00")
					self.assertEqual(data["approval_status"], "Pending Validation")
					self.assertIsNone(data["approved_by"])
