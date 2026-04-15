# Copyright (c) 2026, Techincglobal.com and contributors
import frappe
from frappe.model.document import Document
from frappe.utils import flt


class costItem(Document):

	def validate(self):
		self._sync_calc_unit_costs()
		self._calc_unit_cost_sum()
		self._calc_total_cost()

	# ── Sync unit_cost on each calculation row from CB ─────────
	def _sync_calc_unit_costs(self):
		for row in self.calculations or []:
			if not row.calculation_breakdown:
				continue
			uc = frappe.db.get_value(
				"Calculation Breakdown", row.calculation_breakdown, "unit_cost"
			)
			row.unit_cost = flt(uc)
			row.amount    = round(flt(uc) * flt(self.item_qty), 2)

	# ── unit_cost = sum of all linked CB unit costs ─────────────
	def _calc_unit_cost_sum(self):
		self.unit_cost = round(
			sum(flt(r.unit_cost) for r in (self.calculations or [])), 4
		)

	# ── total_cost = unit_cost × item_qty ──────────────────────
	def _calc_total_cost(self):
		self.total_cost = round(flt(self.unit_cost) * flt(self.item_qty), 2)