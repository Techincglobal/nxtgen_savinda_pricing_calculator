"""Pricing plans use their manufacturing table for the standard Get Items action."""
import math

import frappe
from frappe.utils import flt, now_datetime
from erpnext.manufacturing.doctype.production_plan.production_plan import ProductionPlan


class PricingProductionPlan(ProductionPlan):
	@frappe.whitelist()
	def get_items(self):
		# The initial Sales Order mapper runs before pricing_type is assigned.
		# Ordinary ERPNext plans retain the standard behavior.
		if getattr(self, "_pricing_source_initialization", False) or self.get("custom_pricing_type") not in ("Offset", "Flexo"):
			return super().get_items()
		self.check_permission("write")
		if self.docstatus != 0:
			frappe.throw("Manufacturing items can only be fetched for a draft Production Plan.")
		rows = planning_manufacture_items(self)
		# Validate everything before replacing the existing native rows.
		self.set("po_items", [])
		self.set("prod_plan_references", [])
		self.set("sub_assembly_items", [])
		self.combine_items = 0
		for row in rows:
			self.append("po_items", row)
		self.calculate_total_planned_qty()


def planning_manufacture_items(plan):
	field = "custom_flexo_planning" if plan.get("custom_pricing_type") == "Flexo" else "custom_offset_planning"
	planning = plan.get(field) or []
	if not planning:
		frappe.throw("Add items to the {0} Manufacturing Planning table first.".format(plan.get("custom_pricing_type")))
	result = []
	for index, row in enumerate(planning, 1):
		code, qty = row.get("fg_item"), flt(row.get("qty"))
		if not code or not math.isfinite(qty) or qty <= 0:
			frappe.throw("Manufacturing Planning row {0} needs an FG Item and a positive quantity.".format(index))
		item = frappe.db.get_value("Item", code, ["stock_uom", "description", "disabled"], as_dict=True)
		if not item or item.get("disabled"):
			frappe.throw("Manufacturing Planning row {0}: Item {1} is missing or disabled.".format(index, code))
		bom = row.get("bom_no")
		if not bom:
			from nxtgen_savinda_pricing_calculator.api.production_plan import _active_bom_for_item
			bom = _active_bom_for_item(code)
		if not bom or not frappe.db.exists("BOM", {"name": bom, "item": code, "docstatus": 1, "is_active": 1}):
			frappe.throw("Manufacturing Planning row {0}: select an active submitted BOM for {1}.".format(index, code))
		# Retain an unambiguous SO row link. Do not attach manufactured components
		# to the assembled parent's Sales Order row or guess between duplicate SO rows.
		refs = {(t.get("sales_order"), t.get("sales_order_item")) for t in plan.get("custom_ticket_items") or []
			if t.get("fg_item") == code and t.get("sales_order_item")}
		if not refs:
			refs = {(t.get("sales_order"), t.get("sales_order_item")) for t in plan.get("po_items") or []
				if t.get("item_code") == code and t.get("sales_order_item")}
		so = so_item = None
		factor = 1
		warehouse = plan.get("for_warehouse")
		if len(refs) == 1:
			so, so_item = next(iter(refs))
			source = frappe.db.get_value("Sales Order Item", so_item,
				["parent", "item_code", "conversion_factor", "warehouse"], as_dict=True)
			if source and source.get("parent") == so and source.get("item_code") == code:
				factor = flt(source.get("conversion_factor")) or 1
				warehouse = warehouse or source.get("warehouse")
			else:
				so = so_item = None
		elif len(refs) > 1:
			frappe.throw("Item {0} has multiple Sales Order row references. Use separate Production Plans for these rows to preserve quantities and UOM links.".format(code))
		result.append({"item_code": code, "description": item.get("description") or row.get("item_name") or code,
			"bom_no": bom, "planned_qty": qty * factor, "pending_qty": qty * factor,
			"stock_uom": item.get("stock_uom"), "warehouse": warehouse,
			"sales_order": so, "sales_order_item": so_item,
			"planned_start_date": now_datetime(), "include_exploded_items": 1})
	return result
