# Copyright (c) 2026, Techincglobal.com and contributors
import frappe
from frappe.model.document import Document
from frappe.utils import flt, today


def _company_currency():
	return (
		frappe.db.get_single_value("Global Defaults", "default_currency")
		or frappe.db.get_default("currency")
		or "LKR"
	)


class SavindaQuotation(Document):

	def before_insert(self):
		self._fetch_from_cost_sheet()
		self._fetch_from_inquiry()
		if not self.currency:
			self.currency = _company_currency()
		if not self.conversion_rate:
			self.conversion_rate = 1

	def validate(self):
		if not self.date:
			self.date = today()
		self._validate_currency()
		# Keep customer_name in sync with the Customer link (replaces the old fetch_from,
		# which no longer runs now that the field is editable for lead-based inquiries).
		if self.customer:
			cn = frappe.db.get_value("Customer", self.customer, "customer_name")
			if cn:
				self.customer_name = cn
		self._apply_common_material()
		self._apply_lowest_profit_margin()

	def _apply_lowest_profit_margin(self):
		"""Header profit margin = the LOWEST profit margin among the quoted items'
		Calculation Breakdowns (the most conservative margin across the quotation)."""
		if not self.meta.has_field("profit_margin"):
			return
		margins = []
		for it in (self.items or []):
			if not it.calculation_breakdown:
				continue
			pm = frappe.db.get_value(
				"Calculation Breakdown", it.calculation_breakdown, "profit_margin"
			)
			if pm is not None:
				margins.append(flt(pm))
		if margins:
			self.profit_margin = min(margins)

	def _validate_currency(self):
		"""Costing is in company base (LKR); the quotation may be presented in another
		currency. conversion_rate = LKR per 1 unit of the quotation currency (1 when base)."""
		base = _company_currency()
		if not self.currency:
			self.currency = base
		if self.currency == base:
			self.conversion_rate = 1
		elif not self.conversion_rate or flt(self.conversion_rate) <= 0:
			# Fall back to a stored/auto rate; leave at 1 only if nothing is available.
			self.conversion_rate = flt(self.conversion_rate) or 1

	def _apply_common_material(self):
		"""Show ONLY the Boards-and-Papers common (customer-facing) name on each line — never
		the real material. Resolvable → common name; otherwise blank (the actual material is
		never exposed). Configure common_name on Boards and Papers to show a material."""
		from nxtgen_savinda_pricing_calculator.api.offset_calculator import (
			resolve_common_material_name,
		)
		for it in (self.items or []):
			base_mat = ""
			if it.calculation_breakdown:
				base_mat = frappe.db.get_value(
					"Calculation Breakdown", it.calculation_breakdown, "base_material"
				) or ""
			it.material = resolve_common_material_name(
				raw_material=it.material or "", base_material=base_mat
			) or ""

	# ── Inquiry (Opportunity) lifecycle — mirrors ERPNext Quotation→Opportunity ──
	# The Opportunity status Select allows: Open / Quotation / Converted / Lost / Replied / Closed.
	# We set it DIRECTLY (never via set_status) because ERPNext's status predicates only look at
	# native Quotation/Sales Order records and would not see this custom doctype.
	def on_submit(self):
		# Quotation issued → advance the Inquiry to "Quotation"
		self._update_inquiry_status("Quotation")

	def on_update_after_submit(self):
		# status is allow_on_submit; Mark as Won / Lost changes it after submit
		if not self.has_value_changed("status"):
			return
		if self.status == "Won":
			self._update_inquiry_status("Converted")
		elif self.status == "Lost":
			self._update_inquiry_status("Lost", copy_lost=True)
		elif self.status in ("Sent", "Accepted", "Draft"):
			self._update_inquiry_status("Quotation")

	def on_cancel(self):
		self._update_inquiry_status("Open")

	def _update_inquiry_status(self, status, copy_lost=False):
		if not self.inquiry or not frappe.db.exists("Opportunity", self.inquiry):
			return
		if copy_lost:
			# Needs child rows → load the doc; skip validate to avoid status re-derivation.
			opp = frappe.get_doc("Opportunity", self.inquiry)
			opp.status = "Lost"
			if hasattr(opp, "order_lost_reason"):
				opp.order_lost_reason = self.order_lost_reason or ""
			if hasattr(opp, "lost_reasons"):
				opp.set("lost_reasons", [])
				for r in (self.lost_reasons or []):
					if r.lost_reason:
						opp.append("lost_reasons", {"lost_reason": r.lost_reason})
			opp.flags.ignore_permissions = True
			opp.flags.ignore_validate = True
			opp.save(ignore_permissions=True)
		else:
			frappe.db.set_value("Opportunity", self.inquiry, "status", status)

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
