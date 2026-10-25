import unittest
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator.api import costing_bundle as bundle
from nxtgen_savinda_pricing_calculator.api import fg_approval, manufacturing, offset_calculator, pricing_rule, production_plan


class TestCostingBundle(unittest.TestCase):
	def test_component_loader_includes_readable_item_names(self):
		cs = self.sheet()
		with patch.object(frappe, "get_doc", return_value=cs), patch.object(cs, "check_permission"), patch.object(
			frappe.db, "get_value", side_effect=lambda dt, name, field, **kwargs:
				"Outer box" if dt == "cost Item" and name == "LID" else "Tray" if dt == "cost Item" else "CB-1"
		), patch.object(frappe, "get_all", return_value=[]):
			rows = bundle.component_defaults(cs.name)
		self.assertEqual([r["cost_item_name"] for r in rows], ["Outer box", "Tray"])
		self.assertEqual([r["cost_item"] for r in rows], ["LID", "TRAY"])

	def test_existing_bundle_names_are_displayed_without_database_writes(self):
		from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.cost_sheet.cost_sheet import CostSheet
		cs = self.sheet()
		cs.inquiry = None
		with patch.object(frappe.db, "get_value", side_effect=["Outer box", "Tray"]), patch.object(frappe.db, "set_value") as write:
			CostSheet.onload(cs)
		self.assertEqual([r.cost_item_name for r in cs.custom_bundle_components], ["Outer box", "Tray"])
		write.assert_not_called()

	def sheet(self):
		return frappe.get_doc({"doctype": "Cost Sheet", "name": "TEST-BUNDLE", "pricing_type": "Offset",
			"custom_quote_as_bundle": 1, "custom_bundle_name": "Gift Box", "custom_bundle_qty": 100,
			"custom_bundle_uom": "Nos", "custom_bundle_assembly_cost": 3, "custom_bundle_assembly_price": 5,
			"pricing_list": [{"item": "LID"}, {"item": "TRAY"}],
			"custom_bundle_components": [{"cost_item": "LID", "calculation_breakdown": "CB-LID", "qty_per_bundle": 1},
				{"cost_item": "TRAY", "calculation_breakdown": "CB-TRAY", "qty_per_bundle": 2}],
			"qty_breaks": [{"qty": 500}, {"qty": 100}]})

	def test_components_are_recalculated_at_their_own_ratios(self):
		cs = self.sheet()
		with patch.object(bundle, "validate_bundle"), patch.object(offset_calculator, "calculate_qty_break",
			side_effect=[{"unit_cost": 10, "sell_unit": 15, "profit_margin": 20}, {"unit_cost": 4, "sell_unit": 6, "profit_margin": 12}]) as calc:
			result = bundle.price_bundle(cs, 100)
		self.assertEqual([c.args for c in calc.call_args_list], [("CB-LID", 100), ("CB-TRAY", 200)])
		self.assertEqual(result["unit_cost"], 21)
		self.assertEqual(result["rate"], 32)
		self.assertEqual(result["profit_margin"], 12)

	def test_bundle_finishings_are_distinct_in_component_order(self):
		from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils import jinja
		with patch.object(jinja, "get_cb_finishings", side_effect=[["DIE CUTTING", "Gloss"], [" die cutting ", "Lamination", "Gloss"]]):
			self.assertEqual(bundle.bundle_finishings(self.sheet()), ["DIE CUTTING", "Gloss", "Lamination"])

	def test_quotation_margin_matches_persisted_precision_on_repeat_validation(self):
		q = frappe.get_doc({"doctype": "Savinda Quotation", "cost_sheet": "TEST-BUNDLE",
			"profit_margin": 17.018969158, "items": [{"custom_costing_bundle": "TEST-BUNDLE",
				"qty": 1000, "base_selling_price": 100, "is_manually_set": 1}]})
		with patch.object(frappe, "get_doc", return_value=self.sheet()), patch.object(bundle,
			"price_bundle", return_value={"unit_cost": 82.98103084153047, "rate": 100, "profit_margin": 20}):
			q._apply_lowest_profit_margin()
			self.assertEqual(q.profit_margin, 17.018969158)
			q._apply_lowest_profit_margin()
			self.assertEqual(q.profit_margin, 17.018969158)

	def test_quote_rows_carry_distinct_finishing_labels(self):
		with patch.object(bundle, "bundle_finishings", return_value=["DIE CUTTING", "Lamination"]), patch.object(
			bundle, "price_bundle", side_effect=lambda cs, q: {"qty": q, "rate": 32, "unit_cost": 20}):
			rows = bundle.quote_rows(self.sheet(), "LKR", 1)
		self.assertTrue(all(r["finishing"] == "DIE CUTTING + Lamination" for r in rows))

	def test_new_component_is_named_and_added_to_both_bundle_tables(self):
		cs = self.sheet()
		with patch.object(frappe.db, "exists", return_value=True), patch.object(frappe, "get_doc", return_value=cs), patch.object(
			cs, "check_permission"), patch.object(cs, "save"), patch.object(frappe.db, "commit"), patch.object(
			offset_calculator, "duplicate_cost_item", return_value={"new_cost_item": "INNER", "new_cbs": ["CB-INNER"]}) as duplicate, patch.object(
			frappe.db, "get_value", side_effect=lambda dt, name, fields, **kw: frappe._dict(cost_item_name="Inner Carton", item_qty=100, unit_cost=4) if dt == "cost Item" else frappe._dict(item_qty=100, selling_price=600)):
			result = offset_calculator.create_variant_for_switcher("LID", cs.name, kind="component", new_qty=100, new_name="Inner Carton")
		duplicate.assert_called_once_with("LID", new_name="Inner Carton", new_qty=100)
		self.assertEqual(result["new_cb"], "CB-INNER")
		self.assertEqual(cs.pricing_list[-1].item, "INNER")
		self.assertEqual(cs.custom_bundle_components[-1].cost_item, "INNER")
		self.assertEqual(cs.custom_bundle_components[-1].qty_per_bundle, 1)

	def test_submitted_sheet_is_rejected_before_creating_component(self):
		cs = self.sheet()
		cs.docstatus = 1
		with patch.object(frappe.db, "exists", return_value=True), patch.object(frappe, "get_doc", return_value=cs), patch.object(
			cs, "check_permission"), patch.object(offset_calculator, "duplicate_cost_item") as duplicate:
			result = offset_calculator.create_variant_for_switcher("LID", cs.name, kind="component", new_qty=100, new_name="Inner Carton")
		self.assertIn("draft", result["error"])
		duplicate.assert_not_called()

	def test_one_quotation_product_per_bundle_tier_with_currency_conversion(self):
		cs = self.sheet()
		with patch.object(bundle, "price_bundle", side_effect=lambda cs, q: {"qty": q, "rate": 64, "unit_cost": 40}):
			rows = bundle.quote_rows(cs, "USD", 320)
		self.assertEqual([r["qty"] for r in rows], [100, 500])
		self.assertTrue(all(r["item_name"] == "Gift Box" and r["selling_price"] == .2 for r in rows))
		self.assertTrue(all("cost_item" not in r and "calculation_breakdown" not in r for r in rows))

	def test_calculation_errors_do_not_fall_back_to_component_base_prices(self):
		with patch.object(bundle, "validate_bundle"), patch.object(offset_calculator, "calculate_qty_break", return_value={"error": "Missing state"}), patch.object(
			frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			bundle.price_bundle(self.sheet(), 100)

	def test_mixed_production_types_are_rejected(self):
		with patch.object(frappe.db, "exists", return_value=True), patch.object(frappe.db, "get_value", return_value="Flexo"), patch.object(
			frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			bundle.validate_bundle(self.sheet())

	def test_shared_print_calculation_is_rejected(self):
		cs = self.sheet()
		cs.custom_bundle_components[1].calculation_breakdown = "CB-LID"
		with patch.object(frappe.db, "exists", return_value=True), patch.object(frappe.db, "get_value", return_value="Offset"), patch.object(
			frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			bundle.validate_bundle(cs)

	def test_missing_component_is_rejected(self):
		cs = self.sheet()
		cs.custom_bundle_components.pop()
		with patch.object(frappe.db, "exists", return_value=True), patch.object(frappe.db, "get_value", return_value="Offset"), patch.object(
			frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			bundle.validate_bundle(cs)

	def test_normal_cost_sheet_does_not_enter_bundle_validation(self):
		cs = frappe.get_doc({"doctype": "Cost Sheet"})
		with patch.object(frappe.db, "exists") as query:
			bundle.validate_bundle(cs)
		query.assert_not_called()

	def test_sales_order_lists_only_parent_not_component_items(self):
		doc = Mock()
		doc.items = [frappe._dict(custom_costing_bundle="TEST", finish_good="GIFT-BOX", item_name="Gift Box", qty=100, base_selling_price=32)]
		with patch.object(frappe, "get_doc", return_value=doc), patch.object(frappe.db, "get_value", return_value="GIFT-BOX"), patch.object(frappe, "get_all") as query:
			rows = manufacturing.get_quotation_fg_items("SQ-TEST")
		self.assertEqual([r["item_code"] for r in rows], ["GIFT-BOX"])
		query.assert_not_called()

	def test_bundle_so_without_matching_rule_has_zero_rate(self):
		row = frappe._dict(item_code="GIFT-BOX", qty=100, rate=99)
		doc = frappe._dict(items=[row], conversion_rate=1)
		doc.calculate_taxes_and_totals = Mock()
		with patch.object(frappe.db, "get_value", side_effect=lambda dt, name, field: "TEST" if field == "custom_costing_bundle" else None), patch.object(
			pricing_rule, "_active_auto_rule", return_value=None):
			pricing_rule.enforce_sales_order_pricing(doc)
		self.assertEqual(row.rate, 0)

	def test_assembly_bom_must_agree_with_costing_ratios(self):
		cs = self.sheet()
		cs.custom_bundle_fg = "GIFT-BOX"
		for r in cs.custom_bundle_components:
			r.fg_item = r.cost_item
		bom = frappe._dict(docstatus=1, is_active=1, item="GIFT-BOX", quantity=1)
		bom["items"] = [frappe._dict(item_code="LID", qty=1, conversion_factor=1), frappe._dict(item_code="TRAY", qty=1, conversion_factor=1)]
		# _dict.items is a dict method, whereas actual BOM.items is a child table.
		bom_doc = Mock(docstatus=1, is_active=1, item="GIFT-BOX", quantity=1, items=bom["items"])
		with patch.object(bundle, "validate_bundle"), patch.object(frappe, "get_doc", return_value=bom_doc), patch.object(
			frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
			bundle.validate_assembly_bom(cs, "BOM-TEST")

	def test_parent_pricing_approval_checks_bundle_quantities(self):
		cs = self.sheet()
		pl = frappe._dict(cost_item="", fg_item="GIFT-BOX", custom_costing_bundle=cs.name, customer="CUSTOMER")
		with patch.object(frappe, "get_doc", return_value=cs), patch.object(bundle, "validate_bundle"), patch.object(
			frappe.db, "get_value", return_value=cs.name), patch.object(pricing_rule, "_active_auto_rule", return_value=frappe._dict(rate=32)) as active:
			fg_approval.check_pricing_for_approval(pl)
		self.assertEqual([c.args[1] for c in active.call_args_list], [100, 500])

	def test_bundle_pricing_editor_uses_aggregate_not_first_component(self):
		with patch.object(frappe.db, "get_value", side_effect=lambda dt, name, field: "TEST-BUNDLE" if field == "custom_costing_bundle" else "Gift Box"), patch.object(
			bundle, "pricing_tiers", return_value=[{"qty": 100, "rate": 32}]) as aggregate:
			out = pricing_rule.get_qty_price_tiers("GIFT-BOX")
		aggregate.assert_called_once_with("TEST-BUNDLE")
		self.assertEqual(out["tiers"][0]["rate"], 32)
		self.assertEqual(out["cost_item"], "")

	def test_three_components_keep_independent_planning_data_under_offset_parent(self):
		cs = self.sheet()
		cs.custom_bundle_fg = "GIFT-BOX"
		cs.append("custom_bundle_components", {"cost_item": "INNER", "calculation_breakdown": "CB-INNER", "qty_per_bundle": 1})
		for row in cs.custom_bundle_components:
			row.fg_item = row.cost_item
		pp = Mock(docstatus=0)
		values = {"custom_pricing_type": "Offset", "custom_ticket_items": [frappe._dict(fg_item="GIFT-BOX", qty=100, description="Gift Box")]}
		pp.get.side_effect = values.get
		components = [{"item_code": r.fg_item, "qty": 100 * r.qty_per_bundle, "bom_no": "BOM-" + r.fg_item} for r in cs.custom_bundle_components]
		def plan(cb, qty, pricing):
			return {"ups": 4 if cb == "CB-LID" else 2, "cuts": 1, "full_sheet_qty": qty/2,
				"cut_sheet_qty": qty, "wastage": 10, "full_sheet_size": cb, "cut_sheet_size": cb}
		with patch.object(frappe, "get_doc", side_effect=lambda dt, name: pp if dt == "Production Plan" else cs), patch.object(
			frappe.db, "get_value", side_effect=lambda dt, name, field: cs.name if field == "custom_costing_bundle" else name), patch.object(frappe.db, "exists", return_value=True), patch.object(
			bundle, "validate_assembly_bom"), patch.object(production_plan, "_active_bom_for_item", return_value="BOM-PARENT"), patch.object(
			production_plan, "_bom_manufacturing_components", return_value=components), patch.object(production_plan.jt_api, "_fg_context", return_value={}), patch.object(
			production_plan, "_plan_print_data", side_effect=plan), patch.object(production_plan, "_base_material_for_calculation", side_effect=lambda cb: "BASE-" + cb):
			result = production_plan.add_bom_items_for_manufacture("PP-TEST")
		rows = [c.args[1] for c in pp.append.call_args_list]
		self.assertEqual(result, {"added": 4, "assembly_rows": 1})
		self.assertEqual(rows[0]["is_group"], 1)
		self.assertEqual([r["qty"] for r in rows[1:]], [100, 200, 100])
		self.assertTrue(all(r["parent_item"] == "GIFT-BOX" for r in rows[1:]))
		self.assertEqual(len({r["base_material"] for r in rows[1:]}), 3)
