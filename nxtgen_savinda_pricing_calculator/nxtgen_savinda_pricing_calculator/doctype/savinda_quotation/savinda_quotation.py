# Copyright (c) 2026, Techincglobal.com and contributors
import frappe
from frappe.model.document import Document
from frappe.utils import flt, today


class SavindaQuotation(Document):

	def before_insert(self):
		self._fetch_from_cost_sheet()
		self._fetch_from_inquiry()

	def validate(self):
		if not self.date:
			self.date = today()

	# ── Auto-fill from Cost Sheet ──────────────────────────────
	def _fetch_from_cost_sheet(self):
		if not self.cost_sheet:
			return
		cs = frappe.db.get_value(
			"Cost Sheet", self.cost_sheet,
			["inquiry", "customer_name"],
			as_dict=True,
		)
		if not cs:
			return
		# Fill inquiry
		if not self.inquiry and cs.get("inquiry"):
			self.inquiry = cs["inquiry"]
		# Fill customer — try to find matching Customer by name
		if not self.customer and cs.get("customer_name"):
			# Look up customer by customer_name
			cust = frappe.db.get_value("Customer", {"customer_name": cs["customer_name"]}, "name")
			if cust:
				self.customer = cust
			else:
				# Store in customer_name field directly as fallback
				self.customer_name = cs["customer_name"]

	# ── Auto-fill from Inquiry ─────────────────────────────────
	def _fetch_from_inquiry(self):
		if not self.inquiry:
			return
		opp = frappe.db.get_value(
			"Opportunity", self.inquiry,
			["customer_name"],
			as_dict=True,
		)
		if not opp:
			return
		if not self.customer and opp.get("customer_name"):
			cust = frappe.db.get_value("Customer", {"customer_name": opp["customer_name"]}, "name")
			if cust:
				self.customer = cust