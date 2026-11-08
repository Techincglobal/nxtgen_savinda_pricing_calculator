import unittest
from unittest.mock import Mock, patch
import frappe
from nxtgen_savinda_pricing_calculator.api.production_plan_controller import planning_manufacture_items, PricingProductionPlan
from erpnext.manufacturing.doctype.production_plan.production_plan import ProductionPlan


class TestPlanningFetch(unittest.TestCase):
	def setUp(self):
		if not hasattr(frappe.local, 'db'):
			frappe.local.db = Mock()
			self.addCleanup(lambda: delattr(frappe.local, 'db'))
		self.plan = frappe._dict(custom_pricing_type='Offset', custom_offset_planning=[
			frappe._dict(fg_item='FG1', qty=10, bom_no='BOM1')],
			custom_flexo_planning=[frappe._dict(fg_item='FG2', qty=7, bom_no='BOM2')],
			custom_ticket_items=[], po_items=[])
		self.values = patch.object(frappe.db, 'get_value', side_effect=self.value).start()
		self.exists = patch.object(frappe.db, 'exists', return_value=True).start()
		self.throw = patch.object(frappe, 'throw', side_effect=ValueError).start()
		patch('nxtgen_savinda_pricing_calculator.api.production_plan_controller.now_datetime', return_value='2026-11-08 12:00:00').start()
		self.addCleanup(patch.stopall)

	def value(self, doctype, *args, **kwargs):
		if doctype == 'Item': return frappe._dict(stock_uom='Nos', description='Product', disabled=0)
		return frappe._dict(parent='SO1', item_code='FG1', conversion_factor=2, warehouse='FG-WH')

	def test_offset_excludes_other_table_and_unplanned_order_items(self):
		self.plan.po_items = [frappe._dict(item_code='UNSELECTED', planned_qty=99)]
		rows = planning_manufacture_items(self.plan)
		self.assertEqual([(r['item_code'],r['planned_qty'],r['bom_no']) for r in rows], [('FG1',10,'BOM1')])

	def test_flexo_uses_only_flexo_rows(self):
		self.plan.custom_pricing_type='Flexo'
		rows=planning_manufacture_items(self.plan)
		self.assertEqual([(r['item_code'],r['planned_qty']) for r in rows], [('FG2',7)])

	def test_split_rows_and_selected_boms_preserved(self):
		self.plan.custom_offset_planning.append(frappe._dict(fg_item='FG1',qty=3,bom_no='VARIANT'))
		self.assertEqual([(r['planned_qty'],r['bom_no']) for r in planning_manufacture_items(self.plan)],[(10,'BOM1'),(3,'VARIANT')])

	def test_so_reference_and_stock_uom_conversion(self):
		self.plan.custom_ticket_items=[frappe._dict(fg_item='FG1',sales_order='SO1',sales_order_item='SO-ROW')]
		row=planning_manufacture_items(self.plan)[0]
		self.assertEqual((row['planned_qty'],row['sales_order_item'],row['warehouse']),(20,'SO-ROW','FG-WH'))

	def test_empty_table_does_not_fallback_to_so(self):
		self.plan.custom_offset_planning=[]
		with self.assertRaises(ValueError): planning_manufacture_items(self.plan)

	def test_invalid_bom_and_quantity_rejected(self):
		self.exists.return_value=False
		with self.assertRaises(ValueError): planning_manufacture_items(self.plan)
		self.exists.return_value=True
		self.plan.custom_offset_planning[0].qty=0
		with self.assertRaises(ValueError): planning_manufacture_items(self.plan)

	def test_untyped_initial_mapper_uses_standard_method(self):
		doc=Mock();doc.get.return_value=None
		with patch.object(ProductionPlan,'get_items',return_value='standard') as original:
			# Invoke without needing site metadata/Document initialization.
			obj=object.__new__(PricingProductionPlan)
			object.__setattr__(obj,'get',doc.get)
			result=PricingProductionPlan.get_items.__wrapped__(obj)
			self.assertEqual(result,'standard')
			original.assert_called_once()
