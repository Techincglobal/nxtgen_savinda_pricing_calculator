# Copyright (c) 2026, Techincglobal.com and contributors
from frappe.model.document import Document


class ProductLibrary(Document):
	def validate(self):
		from nxtgen_savinda_pricing_calculator.api.fg_approval import validate_product_library
		validate_product_library(self)

	def on_update(self):
		from nxtgen_savinda_pricing_calculator.api.fg_approval import on_product_library_update
		on_product_library_update(self)
