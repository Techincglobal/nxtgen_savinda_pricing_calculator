import json
import unittest
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api import offset_calculator


class TestFlexoRemarks(unittest.TestCase):
	def setUp(self):
		if not hasattr(frappe.local, "flags"):
			frappe.local.flags = frappe._dict(in_test=False)
			self.addCleanup(lambda: delattr(frappe.local, "flags"))

	def doc(self, form=None):
		return frappe._dict(name="CB-TEST", docstatus=0, pricing_type="Flexo", remarks="Customer finishing instructions",
			ui_state=json.dumps({"form":form}) if form is not None else "")

	def test_remark_loads_without_ui_state(self):
		with patch.object(frappe, "get_doc", return_value=self.doc()):
			loaded = offset_calculator.load_costing("CB-TEST")
		self.assertEqual(loaded["form"]["remarks"], "Customer finishing instructions")

	def test_older_saved_state_gets_native_remark_fallback(self):
		with patch.object(frappe, "get_doc", return_value=self.doc({"pricing_type":"Flexo"})):
			loaded = offset_calculator.load_costing("CB-TEST")
		self.assertEqual(loaded["form"]["remarks"], "Customer finishing instructions")

	def test_explicitly_cleared_remark_and_saved_percentage_are_preserved(self):
		with patch.object(frappe, "get_doc", return_value=self.doc({"pricing_type":"Flexo", "remarks":"", "extra_prod_cost_pct":7})):
			loaded = offset_calculator.load_costing("CB-TEST")
		self.assertEqual(loaded["form"]["remarks"], "")
		self.assertEqual(loaded["form"]["extra_prod_cost_pct"], 7)

	def test_save_persists_remark_in_document_and_calculator_state(self):
		doc = Mock()
		doc.name = "CB-TEST"
		payload = {"form":{"pricing_type":"Flexo", "remarks":"Print and die-cut carefully", "extra_prod_cost_pct":0}}
		with patch.object(frappe, "new_doc", return_value=doc), patch.object(frappe, "db", Mock()):
			offset_calculator.save_costing(payload)
		self.assertEqual(doc.remarks, "Print and die-cut carefully")
		self.assertEqual(doc.extra_prod_cost_pct, 0)
		self.assertEqual(json.loads(doc.ui_state)["form"]["remarks"], doc.remarks)
		doc.insert.assert_called_once()
