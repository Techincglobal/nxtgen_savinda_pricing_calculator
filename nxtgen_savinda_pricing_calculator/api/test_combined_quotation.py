import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api import costing_bundle as bundle
from nxtgen_savinda_pricing_calculator.api import offset_calculator


class TestCombinedQuotation(unittest.TestCase):
	def sheet(self):
		return frappe.get_doc({"doctype":"Cost Sheet", "name":"COST-26-10.00643-1", "custom_quote_as_bundle":1,
			"custom_bundle_name":"Gift Box test", "custom_bundle_qty":1000, "custom_bundle_uom":"Nos",
			"custom_bundle_assembly_cost":0,"custom_bundle_assembly_price":0,
			"custom_bundle_components":[{"cost_item":"CI-A","calculation_breakdown":"CB-A","qty_per_bundle":1},
				{"cost_item":"CI-B","calculation_breakdown":"CB-B","qty_per_bundle":2}]})

	def test_sample_defaults_are_distinct_and_never_use_raw_material_names(self):
		cs = self.sheet()
		cb = frappe._dict(name="CB-A", base_material="PRIVATE-BOARD",no_of_colors=5,carton_size="150 x 40 x 60")
		ci = frappe._dict(dimensions="150 x 40 x 60", bom_remark="Foiled carton")
		with patch.object(frappe,"get_doc",side_effect=lambda dt,name: cb if dt=="Calculation Breakdown" else ci), patch.object(
			offset_calculator,"resolve_common_material_name",return_value="Customer Board"), patch.object(bundle,"bundle_finishings",return_value=["GOLD FOILING","DIE CUTTING"]):
			values = bundle.combined_quote_defaults(cs)
		self.assertEqual(values["custom_combined_title"],"Gift Box test")
		self.assertEqual(values["custom_combined_size"],"150 x 40 x 60")
		self.assertEqual(values["custom_combined_colours"],"5")
		self.assertEqual(values["custom_combined_material"],"Customer Board")
		self.assertNotIn("PRIVATE-BOARD",str(values))

	def test_new_quote_defaults_preserve_explicit_customer_specification(self):
		q=frappe.get_doc({"doctype":"Savinda Quotation","cost_sheet":"COST-26-10.00643-1",
			"currency":"LKR","conversion_rate":1,"custom_combined_remarks":"Customer-approved description"})
		cs=self.sheet();cs.docstatus=1
		with patch.object(q,"_fetch_from_cost_sheet"),patch.object(q,"_fetch_from_inquiry"),patch.object(frappe.db,"get_value",return_value=1),patch.object(
			frappe,"get_doc",return_value=cs),patch.object(cs,"check_permission"),patch.object(bundle,"quote_rows",return_value=[]),patch.object(
			bundle,"combined_quote_defaults",return_value={"custom_combined_quote":1,"custom_combined_remarks":"Autofilled notes"}):
			q.before_insert()
		self.assertEqual(q.custom_combined_remarks,"Customer-approved description")

	def test_existing_print_format_is_never_overwritten(self):
		with patch.object(frappe.db,"exists",return_value=True),patch.object(frappe,"get_doc") as insert:
			bundle._ensure_combined_print_format()
		insert.assert_not_called()

	def test_explicit_migration_sync_updates_only_combined_print_layout(self):
		with patch.object(frappe.db, "exists", return_value=True), patch.object(frappe, "get_doc") as get_doc:
			bundle._ensure_combined_print_format(overwrite=True)
		get_doc.assert_called_once_with("Print Format", "Savinda Combined Item Quotation")
		get_doc.return_value.update.assert_called_once()
		get_doc.return_value.save.assert_called_once_with(ignore_permissions=True)
		self.assertIn("QUOTATION FOR PRINTING OF", get_doc.return_value.update.call_args.args[0]["html"])

	def test_reference_style_print_hides_components_and_converts_no_prices_twice(self):
		q=frappe.get_doc({"doctype":"Savinda Quotation","name":"SQ-TEST","customer_name":"Test Customer",
			"currency":"USD","conversion_rate":300,"custom_combined_title":"Gift Box test","custom_combined_colours":"5",
			"custom_combined_material":"Customer Board","custom_combined_size":"150 x 40 x 60",
			"custom_combined_finishing":"GOLD FOILING, DIE CUTTING","custom_combined_remarks":"CUSTOMER SPEC <script>bad()</script>",
			"vat_note":"VAT excluded","validity_note":"7 Days","items":[{"item_name":"Gift Box test","custom_costing_bundle":"COST-TEST","qty":1000,
				"selling_price":1.25,"base_selling_price":375}]})
		template=Path(frappe.get_app_path("nxtgen_savinda_pricing_calculator","templates","prints","combined_item_quotation.html")).read_text()
		html=frappe.render_template(template,{"doc":q,"letter_head":"","no_letterhead":1})
		self.assertIn("Gift Box test",html)
		self.assertIn("1,000",html)
		self.assertIn("1,250.0000",html)
		self.assertIn("PRICE PER 1,000 UNITS (USD)",html)
		self.assertNotIn("CI-A",html)
		self.assertNotIn("<script>bad()",html)
		self.assertIn("&lt;script&gt;",html)
		self.assertIn("QUOTATION FOR PRINTING OF GIFT BOX TEST",html)
		self.assertIn('class="company-header"',html)
		self.assertIn('class="signature"',html)
		self.assertNotIn('class="footer"',html)
		self.assertIn("VAT excluded",html)
		self.assertNotIn("18.00% VAT",html)

	def test_reference_format_uses_real_tiep_and_selected_letterhead(self):
		q = frappe.get_doc({"doctype":"Savinda Quotation", "name":"SQ-TEST", "cost_sheet":"COST-TEST",
			"items":[{"item_name":"Bundle", "custom_costing_bundle":"COST-TEST", "qty":1200, "selling_price":223.1916}]})
		template = Path(frappe.get_app_path("nxtgen_savinda_pricing_calculator", "templates", "prints", "combined_item_quotation.html")).read_text()
		with patch.object(frappe.db, "get_value", new=lambda *args, **kwargs: "NON TIEP"):
			html = frappe.render_template(template, {"doc":q, "letter_head":"<div>Approved Letterhead</div>", "no_letterhead":0})
		self.assertIn("Approved Letterhead", html)
		self.assertNotIn('class="company-header"', html)
		self.assertIn("<th>TIEP</th>", html)
		self.assertIn("<td>NO</td>", html)
		self.assertIn("223.19", html)

	def test_sample_ratios_price_at_1000_and_2000_not_320000(self):
		cs=self.sheet()
		with patch.object(bundle,"validate_bundle"),patch.object(offset_calculator,"calculate_qty_break",side_effect=[
			{"unit_cost":10,"sell_unit":12,"profit_margin":15}, {"unit_cost":20,"sell_unit":24,"profit_margin":15}]) as calc:
			result=bundle.price_bundle(cs,1000)
		self.assertEqual([call.args[1] for call in calc.call_args_list],[1000,2000])
		self.assertEqual(result["rate"],60)
