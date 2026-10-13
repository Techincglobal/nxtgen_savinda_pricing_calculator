# Copyright (c) 2026, Techincglobal.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class Packing(Document):
	def validate(self):
		self._sync_and_validate_qty()

	def _sync_and_validate_qty(self):
		"""Refresh Order/Delivered/New qty server-side and block packing more than the
		available (New) qty."""
		if not (self.job__npd_number and self.item):
			return
		q = get_packing_quantities(self.job__npd_number, self.item, self.type, self.name)
		self.order_qty = q.get("order_qty")
		self.delivered_qty = q.get("delivered_qty")
		self.new_qty = q.get("new_qty")
		if flt(self.extra) < 0:
			frappe.throw("Extra Qty cannot be negative.")
		self.total = flt(self.packed_qty) + flt(self.extra)
		if self.total > flt(self.new_qty) + 1e-6:
			frappe.throw(
				"Cannot pack more than the available (New) qty. Available: <b>{0}</b>, "
				"trying to pack (including Extra): <b>{1}</b>.".format(flt(self.new_qty), self.total)
			)


def _sum(doctype, filters, field="qty"):
	rows = frappe.get_all(doctype, filters=filters, fields=[field])
	return sum(flt(r.get(field)) for r in rows)


@frappe.whitelist()
def get_pp_items(production_plan):
	"""Item codes that belong to a Production Plan — used to restrict the Item field on
	Packing to only the items in the selected Production Plan (po_items + ticket items)."""
	if not production_plan or not frappe.db.exists("Production Plan", production_plan):
		return []
	items = []
	for r in frappe.get_all(
		"Production Plan Item", filters={"parent": production_plan}, fields=["item_code"]
	):
		if r.item_code:
			items.append(r.item_code)
	for r in frappe.get_all(
		"Job Ticket Item",
		filters={"parent": production_plan, "parenttype": "Production Plan",
		         "parentfield": "custom_ticket_items"},
		fields=["fg_item"],
	):
		if r.fg_item:
			items.append(r.fg_item)
	return sorted(set(items))


def _num(v):
	v = flt(v)
	return int(v) if v == int(v) else v


def _packing_breakdown(packing_name):
	"""'43*1 - 0.02g, 84*1 - 0.06g, ...' from the Packing Details rows."""
	parts = []
	for r in frappe.get_all(
		"Packing Details", filters={"parent": packing_name},
		fields=["no_of_boxes", "pcs_per_box", "weight_of_one_box"], order_by="idx",
	):
		parts.append("%s*%s - %sg" % (_num(r.no_of_boxes), _num(r.pcs_per_box), _num(r.weight_of_one_box)))
	return ", ".join(parts)


@frappe.whitelist()
def get_available_packings(sales_order=None, production_plan=None):
	"""Approved, undelivered Packings for one PP (legacy SO lookup retained)."""
	if production_plan:
		frappe.get_doc("Production Plan", production_plan).check_permission("read")
		pps = [production_plan]
	elif sales_order:
		frappe.get_doc("Sales Order", sales_order).check_permission("read")
		pps = frappe.get_list("Production Plan", filters={"custom_sales_order": sales_order}, pluck="name", limit_page_length=0)
	else:
		return []
	if not pps:
		return []
	out = []
	for pk in frappe.get_list(
		"Packing",
		filters={"job__npd_number": ["in", pps], "is_deliverd": 0, "docstatus": 1},
		fields=["name", "item", "packed_qty", "extra"],
		order_by="creation",
		limit_page_length=0,
	):
		out.append({
			"packing": pk.name,
			"item": pk.item,
			"item_name": frappe.db.get_value("Item", pk.item, "item_name") or pk.item,
			"packed_qty": flt(pk.packed_qty),
			"extra": flt(pk.extra),
			"breakdown": _packing_breakdown(pk.name),
		})
	return out


@frappe.whitelist()
def dispatchable_production_plans(company=None):
	"""Readable plans with submitted, undelivered Packing records."""
	pps = frappe.get_list("Packing", filters={"is_deliverd": 0, "docstatus": 1}, pluck="job__npd_number", limit_page_length=0)
	if not pps:
		return []
	filters = {"name": ["in", list(set(pps))], "docstatus": ["<", 2]}
	if company:
		filters["company"] = company
	return frappe.get_list("Production Plan", filters=filters, pluck="name", order_by="name", limit_page_length=0)


@frappe.whitelist()
def dispatchable_sales_orders():
	"""Sales Orders that have at least one packed-but-not-yet-delivered Packing — i.e. stock
	ready to dispatch. Used to restrict the Sales Order picker on the Delivery Note 'Get from
	Packing' popup so it only lists orders that actually have something to dispatch."""
	pps = set()
	for p in frappe.get_all("Packing", filters={"is_deliverd": 0, "docstatus": 1}, fields=["job__npd_number"]):
		if p.job__npd_number:
			pps.add(p.job__npd_number)
	if not pps:
		return []
	sos = set()
	for r in frappe.get_all(
		"Production Plan", filters={"name": ["in", list(pps)]}, fields=["custom_sales_order"]
	):
		if r.custom_sales_order:
			sos.add(r.custom_sales_order)
	return sorted(sos)


_DN_ROW_SKIP = {
	"name", "idx", "parent", "parentfield", "parenttype", "docstatus", "creation",
	"modified", "modified_by", "owner", "doctype", "__islocal", "__unsaved", "__onload",
}


@frappe.whitelist()
def get_delivery_items_from_packings(packings, production_plan=None, sales_order=None):
	"""PP Packing picker: paid Packed Qty and separate free Extra Qty.
	Keep native Sales Order references via ERPNext's mapper whenever the PP has an SO.
	"""
	import json as _json
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	if isinstance(packings, str):
		packings = _json.loads(packings or "[]")
	if not packings:
		return {"items": [], "customer": ""}
	frappe.has_permission("Delivery Note", "create", throw=True)
	# The user selects a PP, never an SO. Resolve its commercial source internally.
	if not production_plan:
		# Compatibility for existing saved clients; do not allow mixed-plan selections.
		production_plan = frappe.db.get_value("Packing", packings[0], "job__npd_number")
	if not production_plan:
		frappe.throw("Select a Production Plan.")
	pp = frappe.get_doc("Production Plan", production_plan)
	pp.check_permission("read")
	linked_so = pp.get("custom_sales_order")
	if sales_order and sales_order != linked_so:
		frappe.throw("The Sales Order does not belong to the selected Production Plan.")
	sales_order = linked_so
	selected = []
	for pk in dict.fromkeys(packings):
		d = frappe.get_doc("Packing", pk)
		d.check_permission("read")
		if d.job__npd_number != production_plan:
			frappe.throw(f"Packing {pk} does not belong to Production Plan {production_plan}.")
		if d.docstatus != 1 or d.is_deliverd:
			frappe.throw(f"Packing {pk} must be approved and not already delivered.")
		if flt(d.packed_qty) < 0 or flt(d.extra) < 0:
			frappe.throw(f"Packing {pk} has a negative quantity.")
		selected.append(d)

	if sales_order:
		frappe.get_doc("Sales Order", sales_order).check_permission("read")
		dn = make_delivery_note(sales_order)
	else:
		# NPD plans need no invented Sales Order. Use normal ERPNext Item defaults.
		customer = pp.get("custom_customer")
		if not customer and pp.get("custom_npd_request"):
			customer = frappe.db.get_value("NPD Request", pp.custom_npd_request, "customer")
		if not customer:
			frappe.throw("Set a Customer on the Production Plan / NPD Request before creating a Delivery Note.")
		dn = frappe.new_doc("Delivery Note")
		dn.company, dn.customer = pp.company, customer
		for d in selected:
			dn.append("items", {"item_code": d.item, "qty": flt(d.packed_qty) or flt(d.extra)})
		dn.set_missing_values()
	so_item = {}
	for it in dn.items:
		so_item.setdefault(it.item_code, it)

	rows = []
	for d in selected:
		pk = d.name
		tmpl = so_item.get(d.item)
		if tmpl:
			row = {k: v for k, v in tmpl.as_dict().items() if k not in _DN_ROW_SKIP}
		else:
			# SO item fully delivered / not in mapper — pull rate/uom straight from the SO line.
			soi = frappe.db.get_value(
				"Sales Order Item", {"parent": sales_order, "item_code": d.item},
				["name", "rate", "uom", "conversion_factor", "warehouse",
				 "item_name", "description", "stock_uom"], as_dict=True,
			) or {}
			if not soi:
				frappe.throw(f"Packing {pk}: Item {d.item} is not on the linked Sales Order.")
			row = {"item_code": d.item, "against_sales_order": sales_order}
			row.update({k: v for k, v in soi.items() if v not in (None, "") and k != "name"})
			if soi.get("name"):
				row["so_detail"] = soi["name"]
		row["qty"] = flt(d.packed_qty)
		row["stock_qty"] = row["qty"] * (flt(row.get("conversion_factor")) or 1)
		row["against_sales_order"] = sales_order
		row["custom_packing"] = pk
		row["is_free_item"] = 0
		if flt(d.packed_qty) > 0:
			rows.append(row)
		if flt(d.extra) > 0:
			extra = dict(row, qty=flt(d.extra), is_free_item=1)
			extra["stock_qty"] = flt(d.extra) * (flt(row.get("conversion_factor")) or 1)
			extra["description"] = (row.get("description") or row.get("item_name") or d.item) + "<br>Extra quantity — free of charge"
			_zero_extra_rate(extra)
			rows.append(extra)
	fields = ("customer", "company", "currency", "conversion_rate", "selling_price_list", "price_list_currency", "plc_conversion_rate", "project")
	return {"items": rows, "customer": dn.customer, "production_plan": production_plan,
		"packing_count": len(selected), "header": {key: dn.get(key) for key in fields if dn.get(key) is not None}}


def _zero_extra_rate(row):
	"""Clear copied commercial values; retain SO, UOM and warehouse references."""
	values = {field: 0 for field in ("rate", "base_rate", "price_list_rate", "base_price_list_rate", "amount", "base_amount",
		"net_rate", "base_net_rate", "net_amount", "base_net_amount", "rate_with_margin",
		"base_rate_with_margin", "margin_rate_or_amount", "discount_amount", "distributed_discount_amount")}
	# 100% discount prevents ERPNext from restoring a fetched price-list rate.
	values.update(discount_percentage=100, margin_type="", pricing_rules="")
	row.update(values)


def enforce_packing_extra_rates(doc, method=None):
	"""Packing-generated extra rows stay free during save/submit and repricing."""
	if doc.get("is_return"):
		return
	changed = False
	for row in doc.get("items") or []:
		if row.get("custom_packing") and row.get("is_free_item"):
			_zero_extra_rate(row)
			changed = True
	if changed and method == "validate":
		doc.calculate_taxes_and_totals()


def mark_packings_delivered(doc, method=None):
	"""Delivery Note hook: flag the linked Packing records delivered on submit, and clear
	the flag on cancel (so their stock is released back to 'New' qty)."""
	delivered = 1 if method != "on_cancel" else 0
	seen = set()
	for it in (doc.get("items") or []):
		pk = it.get("custom_packing")
		if pk and pk not in seen and frappe.db.exists("Packing", pk):
			seen.add(pk)
			frappe.db.set_value("Packing", pk, "is_deliverd", delivered)


@frappe.whitelist()
def get_packing_quantities(production_plan, item, packing_type=None, current_packing=None):
	"""Order / Delivered / New qty for a Packing record.

	- order_qty     : ordered qty for the item — from the Sales Order (Job) or the NPD
	                  Request (NPD) behind the Production Plan.
	- delivered_qty : qty already delivered — submitted Delivery Notes raised against the
	                  same Sales Order for this item.
	- new_qty       : stock on hand that is neither delivered nor earmarked by another
	                  not-delivered Packing record (still free to pack)
	                  = stock across warehouses - packed + extra qty on other undelivered Packings.
	"""
	out = {"order_qty": 0.0, "delivered_qty": 0.0, "new_qty": 0.0}
	if not production_plan or not item:
		return out

	pp = frappe.db.get_value(
		"Production Plan", production_plan,
		["custom_ticket_type", "custom_sales_order", "custom_npd_request"], as_dict=True,
	) or {}
	ttype = (packing_type or pp.get("custom_ticket_type") or "").strip()
	so = pp.get("custom_sales_order")
	npd = pp.get("custom_npd_request")

	# ── Order Qty — from the SO (Job) or the NPD Request (NPD) for this item ──
	if ttype == "NPD" or (not ttype and not so and npd):
		if npd:
			out["order_qty"] = _sum("NPD Request Item", {"parent": npd, "fg_item": item})
	elif so:
		out["order_qty"] = _sum("Sales Order Item", {"parent": so, "item_code": item})
	# Fallback to whichever source has the qty, if the primary was empty.
	if not out["order_qty"]:
		if so:
			out["order_qty"] = _sum("Sales Order Item", {"parent": so, "item_code": item})
		elif npd:
			out["order_qty"] = _sum("NPD Request Item", {"parent": npd, "fg_item": item})

	# ── Delivered Qty — submitted Delivery Notes against the same SO ──
	if so:
		out["delivered_qty"] = _sum(
			"Delivery Note Item",
			{"against_sales_order": so, "item_code": item, "docstatus": 1},
		)

	# ── New Qty — free stock: on hand minus other undelivered packings ──
	available = _sum("Bin", {"item_code": item}, field="actual_qty")
	earmarked = _sum(
		"Packing",
		{"item": item, "is_deliverd": 0, "docstatus": 1, "name": ["!=", current_packing or "__none__"]},
		field="packed_qty",
	)
	earmarked += _sum(
		"Packing",
		{"item": item, "is_deliverd": 0, "docstatus": 1, "name": ["!=", current_packing or "__none__"]},
		field="extra",
	)
	out["new_qty"] = max(available - earmarked, 0.0)
	return out
