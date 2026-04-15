# Copyright (c) 2026, Techincglobal.com and contributors
import frappe
from frappe.model.document import Document
from frappe.utils import flt, cint


class CostSheet(Document):

	def onload(self):
		"""Fetch inquiry data when form loads."""
		if self.inquiry:
			self._fetch_inquiry_fields()

	def before_insert(self):
		if self.inquiry:
			self._fetch_inquiry_fields()

	def validate(self):
		self._calc_pricing_list_amounts()

	# ── Fetch header fields from linked Inquiry ────────────────
	def _fetch_inquiry_fields(self):
		opp = frappe.db.get_value(
			"Opportunity",
			self.inquiry,
			["custom_subject", "customer_name", "custom_colour",
			 "custom_item_group", "custom_tiep"],
			as_dict=True,
		)
		if not opp:
			return
		if opp.get("custom_subject")  and not self.subject:
			self.subject       = opp["custom_subject"]
		if opp.get("customer_name")   and not self.customer_name:
			self.customer_name = opp["customer_name"]
		if opp.get("custom_colour")   and not self.colour:
			self.colour        = cint(opp["custom_colour"])
		if opp.get("custom_item_group") and not self.item_group:
			self.item_group    = opp["custom_item_group"]
		if opp.get("custom_tiep")     and not self.tiep:
			self.tiep          = opp["custom_tiep"]

	# ── Calculate amounts in pricing_list child table ──────────
	def _calc_pricing_list_amounts(self):
		for row in self.pricing_list or []:
			qty        = flt(row.qty)
			unit_price = flt(row.unit_price)
			row.ammount = round(qty * unit_price, 2)

			# Selling amount with optional SSCL and VAT
			sell_unit  = flt(row.selling_unit_price)
			if cint(row.sscl):
				sell_unit = sell_unit * 1.025
			if cint(row.vat):
				sell_unit = sell_unit * 1.18
			row.selling_ammount = round(qty * sell_unit, 2)