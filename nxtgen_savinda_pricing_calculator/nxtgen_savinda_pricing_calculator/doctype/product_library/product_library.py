# Copyright (c) 2026, Techincglobal.com and contributors
import frappe
from frappe.model.document import Document


class ProductLibrary(Document):
	def validate(self):
		if self.get("pricing_quotation_item"):
			parent = frappe.db.get_value("Savinda Quotation Item", self.pricing_quotation_item, "parent")
			if not self.get("quotation_no") or parent != self.quotation_no:
				frappe.throw("Pricing Quotation Item must belong to the selected Quotation No.")
		from nxtgen_savinda_pricing_calculator.api.fg_approval import validate_product_library
		validate_product_library(self)

	def on_update(self):
		from nxtgen_savinda_pricing_calculator.api.fg_approval import on_product_library_update
		on_product_library_update(self)
