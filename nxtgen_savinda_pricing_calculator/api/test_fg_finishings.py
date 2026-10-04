import unittest
from unittest.mock import patch

import frappe

from nxtgen_savinda_pricing_calculator.api import manufacturing


class TestFGFinishings(unittest.TestCase):
	def test_reviewed_rows_are_cleaned_without_mutating_input(self):
		original = {"finishings": [{"process_name": " DIE CUTTING ", "name": "dialog-row"}]}
		result = manufacturing._reviewed_pl_overrides(original)
		self.assertEqual(result["finishings"], [{
			"process_name": "DIE CUTTING", "machine_name": "", "remarks": "",
		}])
		self.assertEqual(result["finishings_reviewed"], 1)
		self.assertEqual(original["finishings"][0]["process_name"], " DIE CUTTING ")

	def test_empty_selection_is_reviewed(self):
		self.assertEqual(manufacturing._reviewed_pl_overrides('{"finishings": []}'), {
			"finishings": [], "finishings_reviewed": 1,
		})

	def test_legacy_call_does_not_mark_finishings_reviewed(self):
		self.assertEqual(manufacturing._reviewed_pl_overrides({"customer": "TEST"}), {
			"customer": "TEST",
		})

	def test_invalid_or_duplicate_rows_are_rejected(self):
		for rows in (None, [{}], [{"process_name": "CUT"}, {"process_name": " CUT "}]):
			with self.subTest(rows=rows), patch.object(
				frappe, "throw", side_effect=frappe.ValidationError
			), self.assertRaises(frappe.ValidationError):
				manufacturing._reviewed_pl_overrides({"finishings": rows})

	def test_sync_leaves_reviewed_finishings_untouched(self):
		for rows in ([], [{"process_name": "CUT"}]):
			pl = frappe._dict(finishings_reviewed=1, finishings=rows)
			with patch.object(frappe, "get_doc", return_value=pl):
				manufacturing._sync_pl_finishings_from_cost_item("TEST-PL", "TEST-CI")
			self.assertEqual(pl.finishings, rows)
