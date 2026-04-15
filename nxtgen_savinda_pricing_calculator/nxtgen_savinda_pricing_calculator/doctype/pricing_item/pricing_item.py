# Copyright (c) 2026, Techincglobal.com and contributors
import frappe
from frappe.model.document import Document
from frappe.utils import flt, cint


class PricingItem(Document):

	def validate(self):
		self._calc_amounts()
		self._calc_cost_breakdown_amounts()

	# ── Main pricing amounts ───────────────────────────────────
	def _calc_amounts(self):
		qty       = flt(self.qty)
		unit      = flt(self.unit_price)
		sell_unit = flt(self.selling_unit_price)

		self.ammount = round(qty * unit, 2)

		# Apply taxes to selling unit price
		taxed = sell_unit
		if cint(self.sscl): taxed = taxed * 1.025
		if cint(self.vat):  taxed = taxed * 1.18
		self.selling_ammount = round(qty * taxed, 2)

	# ── Cost breakdown child table amounts ─────────────────────
	def _calc_cost_breakdown_amounts(self):
		for row in self.cost_brackdown or []:
			row.amount = round(flt(row.qty) * flt(row.unit__price), 2)

			# Sync unit_price and description from linked Calculation Breakdown
			if row.calculation_brakedown and not row.unit__price:
				uc = frappe.db.get_value(
					"Calculation Breakdown",
					row.calculation_brakedown,
					"unit_cost",
				)
				if uc:
					row.unit__price = flt(uc)
					row.amount      = round(flt(row.qty) * flt(uc), 2)