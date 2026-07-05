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
		# Keep customer_name in sync with the Customer link (replaces the old fetch_from,
		# which no longer runs now that the field is editable for lead-based inquiries).
		if self.customer:
			cn = frappe.db.get_value("Customer", self.customer, "customer_name")
			if cn:
				self.customer_name = cn

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
		# Always carry the party name so the quotation has a display name
		if cs.get("customer_name") and not self.customer_name:
			self.customer_name = cs["customer_name"]
		# Link a real Customer only if one matches by name (lead-based → stays blank)
		if not self.customer and cs.get("customer_name"):
			cust = frappe.db.get_value("Customer", {"customer_name": cs["customer_name"]}, "name")
			if cust:
				self.customer = cust

	# ── Auto-fill from Inquiry (Lead / Customer / Prospect) ────
	def _fetch_from_inquiry(self):
		if not self.inquiry:
			return
		opp = frappe.db.get_value(
			"Opportunity", self.inquiry,
			["customer_name", "party_name", "opportunity_from"],
			as_dict=True,
		)
		if not opp:
			return

		# Resolve a display name regardless of party type (Lead / Customer / Prospect)
		name  = opp.get("customer_name")
		party = opp.get("party_name")
		pfrom = opp.get("opportunity_from")
		if not name and party:
			if pfrom == "Lead":
				name = (frappe.db.get_value("Lead", party, "company_name")
				        or frappe.db.get_value("Lead", party, "lead_name") or party)
			elif pfrom == "Prospect":
				name = frappe.db.get_value("Prospect", party, "company_name") or party
			elif pfrom == "Customer":
				name = frappe.db.get_value("Customer", party, "customer_name") or party
			else:
				name = party

		if name and not self.customer_name:
			self.customer_name = name

		# Link a real Customer only when the inquiry is customer-based (or the name matches one)
		if not self.customer:
			if pfrom == "Customer" and party and frappe.db.exists("Customer", party):
				self.customer = party
			elif name:
				cust = frappe.db.get_value("Customer", {"customer_name": name}, "name")
				if cust:
					self.customer = cust
