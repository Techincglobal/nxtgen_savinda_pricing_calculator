import unittest
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api import inquiry_combined_costing as api


class TestInquiryCombinedCosting(unittest.TestCase):
	def setUp(self):
		if not hasattr(frappe.local, "flags"):
			frappe.local.flags = frappe._dict(in_test=False)
			self.addCleanup(lambda: delattr(frappe.local, "flags"))

	def opportunity(self):
		return frappe._dict(name="OPP-1", custom_subject="Gift Box", custom_item_qty=12000,
			custom_colour=6, custom_dimensions="250*150*360", custom_gift_box_types=[
				frappe._dict(name="ROW-1", row_key="key-1", type="Outer box", quantity=1200, custom_item="BOARD-A"),
				frappe._dict(name="ROW-2", row_key="key-2", type="Inner carton", quantity=7200, custom_item="BOARD-B"),
				frappe._dict(name="ROW-3", row_key="key-3", type="Bottom tray", quantity=1200, custom_item="BOARD-A"),
			], custom_gift_box_finishings=[
				frappe._dict(page_row_name="key-1", finishing="FOIL", checked=1),
				frappe._dict(page_row_name="key-2", finishing="VARNISH", checked=1),
				frappe._dict(page_row_name="key-3", finishing="LAMINATION", checked=1),
				frappe._dict(page_row_name="key-1", finishing="FOIL", checked=1),
				frappe._dict(page_row_name="key-1", finishing="EMBOSS", checked=0),
			])

	def test_gift_quantities_materials_and_finishings_remain_independent(self):
		parts = api.inquiry_components(self.opportunity(), "Gift Box")
		self.assertEqual([p["qty"] for p in parts], [1200, 7200, 1200])
		self.assertEqual([p["base_material"] for p in parts], ["BOARD-A", "BOARD-B", "BOARD-A"])
		self.assertEqual([p["finishings"] for p in parts], [["FOIL"], ["VARNISH"], ["LAMINATION"]])

	def test_book_pages_use_existing_page_quantity_semantics_and_own_table(self):
		opp = self.opportunity()
		opp.custom_item_qty = 0
		opp.custom_breakdown = [frappe._dict(qty=100), frappe._dict(qty=200)]
		opp.custom_pages = [frappe._dict(name="P1", row_key="page-1", page_type="Cover Page",
			no_of_pages=2, colour="4", material="Coated Board", dimensions="15x20")]
		opp.custom_page_finishings = [frappe._dict(page_row_name="page-1", finishing="GLOSS", checked=1)]
		part = api.inquiry_components(opp, "Book")[0]
		self.assertEqual(part["qty"], 600)
		self.assertEqual(part["material"], "Coated Board")
		self.assertEqual(part["finishings"], ["GLOSS"])
		self.assertEqual(part["dimensions"], "15x20")

	def test_finishing_join_supports_saved_row_name_without_row_key(self):
		opp = self.opportunity()
		opp.custom_gift_box_finishings = [frappe._dict(page_row_name="ROW-1", finishing="FOIL", checked=1)]
		self.assertEqual(api.inquiry_components(opp, "Gift Box")[0]["finishings"], ["FOIL"])

	def test_unknown_source_and_zero_component_quantity_are_rejected(self):
		with patch.object(frappe, "throw", side_effect=ValueError):
			with self.assertRaises(ValueError):
				api.inquiry_components(self.opportunity(), "Unknown")
			opp = self.opportunity()
			opp.custom_gift_box_types[0].quantity = 0
			with self.assertRaises(ValueError):
				api.inquiry_components(opp, "Gift Box")

	def cost_sheet(self):
		cs = Mock(name="Cost Sheet")
		cs.name = "CS-1"
		cs.inquiry = "OPP-1"
		cs.docstatus = 0
		cs.subject = "Gift Box"
		cs.pricing_type = "Offset"
		cs.customer_name = "Customer"
		cs.get.return_value = []
		return cs

	def test_create_only_pricing_items_with_their_own_finishings(self):
		cs = self.cost_sheet()
		opp = self.opportunity()
		opp.check_permission = Mock()
		inserted = []
		def get_doc(doctype, *args, **kwargs):
			if doctype == "Cost Sheet":
				self.assertTrue(kwargs.get("for_update"))
				return cs
			if doctype == "Opportunity":
				return opp
			doc = Mock()
			doc.name = f"DOC-{len(inserted)}"
			doc.cost_item_name = doctype.get("cost_item_name")
			doc.insert.return_value = doc
			inserted.append(doctype)
			return doc
		with patch.object(frappe, "get_doc", side_effect=get_doc), patch.object(frappe, "db", Mock()) as db, patch.object(
			frappe, "has_permission", return_value=True):
			db.exists.return_value = True
			result = api.create("CS-1", "Gift Box", '["ROW-1", "ROW-2", "ROW-3"]')
		self.assertEqual(len(result["created"]), 3)
		self.assertEqual([d["doctype"] for d in inserted], ["cost Item"] * 3)
		self.assertEqual([d["inquiry_finishings"] for d in inserted], [
			[{"finishing": "FOIL"}], [{"finishing": "VARNISH"}], [{"finishing": "LAMINATION"}]])
		self.assertTrue(all("calculations" not in d for d in inserted))
		self.assertEqual([call.args[0] for call in cs.append.call_args_list], ["pricing_list"] * 3)
		self.assertEqual([call.args[1]["inquiry_finishings"] for call in cs.append.call_args_list], ["FOIL", "VARNISH", "LAMINATION"])
		self.assertNotIn("custom_quote_as_bundle", cs.__dict__)
		cs.save.assert_called_once()

	def test_existing_costing_is_never_replaced(self):
		cs = self.cost_sheet()
		cs.get.return_value = [frappe._dict(item="EXISTING")]
		with patch.object(frappe, "get_doc", return_value=cs), patch.object(frappe, "throw", side_effect=ValueError) as reject:
			with self.assertRaises(ValueError):
				api.create("CS-1", "Gift Box", '["ROW-1"]')
			self.assertIn("Existing costing is never replaced", reject.call_args.args[0])
		cs.append.assert_not_called()
		cs.save.assert_not_called()
