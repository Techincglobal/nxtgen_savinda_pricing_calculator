# Copyright (c) 2026, Techincglobal.com and contributors
# For license information, please see license.txt

import json
import math
import frappe
from frappe.model.document import Document
from frappe.utils import flt, cint


class CalculationBreakdown(Document):

	def validate(self):
		self._resolve_price_list()
		self._default_carton_size()
		self._calc_sheet_requirements()
		self._calc_cost_fact_amounts()
		self._calc_pricing_summary()
		self._render_pricing_html()

	def before_submit(self):
		self.validate()

	# ── 1. Price list ───────────────────────────────────────────
	def _resolve_price_list(self):
		if self.price_list:
			return
		default_pl = frappe.db.get_single_value("Selling Settings", "selling_price_list")
		if default_pl:
			self.price_list = default_pl

	# ── 1b. Carton size ─────────────────────────────────────────
	def _default_carton_size(self):
		"""Fall back to the Inquiry's dimensions — Offset and Flexo alike.

		`ref` holds the Inquiry: its name when the breakdown came from a Cost
		Sheet, or the subject when it was typed into the calculator by hand.
		"""
		if (self.carton_size or "").strip() or not (self.ref or "").strip():
			return

		ref = self.ref.strip()
		if frappe.db.exists("Opportunity", ref):
			dimensions = frappe.db.get_value("Opportunity", ref, "custom_dimensions")
		else:
			dimensions = frappe.db.get_value("Opportunity", {"custom_subject": ref}, "custom_dimensions")
		if dimensions:
			self.carton_size = dimensions.strip()

	# ── 2. Sheet requirements ───────────────────────────────────
	def _calc_sheet_requirements(self):
		no_cuts  = max(cint(self.no_of_cuts)  or 1, 1)
		no_ups   = max(cint(self.no_of_ups)   or 1, 1)
		item_qty = flt(self.item_qty or 0)

		if not item_qty:
			return

		cut_sheet_ups  = max(no_ups // no_cuts, 1)
		cut_sheet_qty  = math.ceil(item_qty / cut_sheet_ups)
		wastage        = max(math.ceil(cut_sheet_qty * 0.05), 500)
		req_cut_sheets = cut_sheet_qty + wastage
		full_sheet_qty = math.ceil(req_cut_sheets / no_cuts)

		self.cut_sheet_ups  = cut_sheet_ups
		self.cut_sheet_qty  = cut_sheet_qty
		self.wastage        = wastage
		self.req_cut_sheets = req_cut_sheets
		self.full_sheet_qty = full_sheet_qty

	# ── 3. Cost fact row amounts ────────────────────────────────
	def _calc_cost_fact_amounts(self):
		"""amount = req_qty × rate for every Cost Fact Details row."""
		for row in self.cost_facts or []:
			row.amount = round(flt(row.req_qty) * flt(row.rate), 2)

	# ── 4. Pricing summary ──────────────────────────────────────
	def _calc_pricing_summary(self):
		"""
		Total cost = base_material cost + all cost_fact amounts
		unit_cost  = total / item_qty
		sscl       = unit_cost × 2.5%   (if tax_sscl)
		quoted     = (unit_cost + sscl) × (1 + profit_margin%)
		vat        = quoted × 18%        (if tax_vat)
		selling    = (quoted + vat) × item_qty
		"""
		item_qty = flt(self.item_qty or 0)
		if not item_qty:
			self.unit_cost     = 0
			self.selling_price = 0
			return

		# Base material cost (full_sheet_qty × material_rate)
		mat_cost = flt(self.full_sheet_qty or 0) * flt(self.material_rate or 0)

		# Sum of all cost_fact rows
		fact_total = sum(flt(r.amount) for r in (self.cost_facts or []))

		grand = mat_cost + fact_total

		uc     = grand / item_qty
		sscl   = uc * 0.025 if self.tax_sscl else 0
		pm     = flt(self.profit_margin or 0) / 100
		quoted = (uc + sscl) * (1 + pm)
		vat    = quoted * 0.18 if self.tax_vat else 0
		sell_u = quoted + vat

		self.unit_cost     = flt(uc,             4)
		self.selling_price = flt(sell_u * item_qty, 2)

	# ── 5. Render pricing HTML summary ─────────────────────────
	def _render_pricing_html(self):
		"""Render a clean HTML pricing summary for the form view."""
		item_qty = flt(self.item_qty or 0)
		if not item_qty:
			return

		mat_cost   = flt(self.full_sheet_qty or 0) * flt(self.material_rate or 0)
		fact_total = sum(flt(r.amount) for r in (self.cost_facts or []))
		grand      = mat_cost + fact_total

		uc     = grand / item_qty
		sscl   = uc * 0.025 if self.tax_sscl else 0
		pm     = flt(self.profit_margin or 0) / 100
		quoted = (uc + sscl) * (1 + pm)
		vat    = quoted * 0.18 if self.tax_vat else 0
		sell_u = quoted + vat

		def fmt(v):
			return f"{flt(v, 2):,.2f}"

		rows_html = ""
		if mat_cost:
			rows_html += f"<tr><td>Base Material</td><td class='r'>LKR {fmt(mat_cost)}</td></tr>"

		# Group cost facts by cost_group
		groups = {}
		for r in (self.cost_facts or []):
			g = r.cost_group or "Other"
			groups[g] = groups.get(g, 0) + flt(r.amount)
		for g, amt in groups.items():
			rows_html += f"<tr><td>{g}</td><td class='r'>LKR {fmt(amt)}</td></tr>"

		price_rows = f"<tr><td>Unit Cost</td><td class='r'>LKR {fmt(uc)}</td><td class='r'>LKR {fmt(grand)}</td></tr>"
		if self.tax_sscl:
			price_rows += f"<tr><td>SSCL (2.5%)</td><td class='r'>LKR {fmt(sscl)}</td><td class='r'>LKR {fmt(sscl*item_qty)}</td></tr>"
		pm_pct = flt(self.profit_margin or 0)
		price_rows += f"<tr><td>Quoted ({pm_pct}% margin)</td><td class='r'>LKR {fmt(quoted)}</td><td class='r'>LKR {fmt(quoted*item_qty)}</td></tr>"
		if self.tax_vat:
			price_rows += f"<tr><td>VAT (18%)</td><td class='r'>LKR {fmt(vat)}</td><td class='r'>LKR {fmt(vat*item_qty)}</td></tr>"

		html = f"""
<style>
.cb-sum-tbl{{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0}}
.cb-sum-tbl th{{background:#1a3a5c;color:#fff;padding:6px 12px;text-align:left;font-size:11px}}
.cb-sum-tbl th.r,.cb-sum-tbl td.r{{text-align:right}}
.cb-sum-tbl td{{padding:5px 12px;border-bottom:1px solid #f0f0f0}}
.cb-sum-tbl tr:nth-child(even) td{{background:#f8fafc}}
.cb-tot td{{background:#1a3a5c!important;color:#fff!important;font-weight:700}}
.cb-sell-badge{{display:inline-block;background:#28a745;color:#fff;border-radius:4px;
  padding:5px 18px;font-size:14px;font-weight:700;margin-top:10px}}
</style>
<table class="cb-sum-tbl">
  <thead><tr><th>Cost Group</th><th class="r">Amount (LKR)</th></tr></thead>
  <tbody>
    {rows_html}
    <tr class="cb-tot"><td><strong>TOTAL COST</strong></td><td class="r">LKR {fmt(grand)}</td></tr>
  </tbody>
</table>
<table class="cb-sum-tbl" style="margin-top:10px">
  <thead><tr><th>Pricing (Qty: {int(item_qty):,})</th><th class="r">Per Unit</th><th class="r">Total</th></tr></thead>
  <tbody>
    {price_rows}
    <tr class="cb-tot">
      <td><strong>Selling Price</strong></td>
      <td class="r">LKR {fmt(sell_u)}</td>
      <td class="r">LKR {fmt(sell_u*item_qty)}</td>
    </tr>
  </tbody>
</table>
<div class="cb-sell-badge">Unit Sell Price: LKR {fmt(sell_u)}</div>
"""
		if hasattr(self, "pricing_summary"):
			self.pricing_summary = html


# ── Whitelisted APIs ────────────────────────────────────────────

@frappe.whitelist()
def get_item_rate(item_code, price_list=None):
	"""Rate priority: Price List → valuation_rate → 0"""
	if not item_code:
		return {"rate": 0, "source": "zero"}

	if price_list:
		rate = frappe.db.get_value(
			"Item Price",
			{"item_code": item_code, "price_list": price_list, "selling": 1},
			"price_list_rate",
			order_by="valid_from desc",
		)
		if rate:
			return {"rate": flt(rate), "source": "price_list"}

	val_rate = frappe.db.get_value("Item", item_code, "valuation_rate")
	if val_rate:
		return {"rate": flt(val_rate), "source": "valuation_rate"}

	return {"rate": 0, "source": "zero"}