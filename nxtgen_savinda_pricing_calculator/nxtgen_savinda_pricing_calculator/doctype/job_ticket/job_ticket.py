# Copyright (c) 2026, Techincglobal.com and contributors
import frappe
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import now


class JobTicket(Document):

	def autoname(self):
		# One doctype, two series driven by ticket_type (naming happens before before_insert).
		self.naming_series = "NPD-.YYYY.-.#####" if self.ticket_type == "NPD" else "PP-.YYYY.-.#####"
		self.name = make_autoname(self.naming_series, doc=self)

	def before_insert(self):
		if not self.created_signature:
			self.created_signature = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
		if not self.created_on:
			self.created_on = now()

	def validate(self):
		if self.customer and not self.customer_name:
			self.customer_name = frappe.db.get_value("Customer", self.customer, "customer_name") or self.customer_name
