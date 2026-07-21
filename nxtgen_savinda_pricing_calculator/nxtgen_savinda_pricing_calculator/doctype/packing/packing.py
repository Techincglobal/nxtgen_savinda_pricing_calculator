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
		if flt(self.packed_qty) > flt(self.new_qty) + 1e-6:
			frappe.throw(
				"Cannot pack more than the available (New) qty. Available: <b>{0}</b>, "
				"trying to pack: <b>{1}</b>.".format(flt(self.new_qty), flt(self.packed_qty))
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
def get_available_packings(sales_order):
	"""Available (not-yet-delivered) Packing records for a Sales Order — i.e. packings whose
	Production Plan is linked to this SO. Used by the Delivery Note 'Get from Packing' popup."""
	if not sales_order:
		return []
	pps = [p.name for p in frappe.get_all(
		"Production Plan", filters={"custom_sales_order": sales_order}, fields=["name"])]
	if not pps:
		return []
	out = []
	for pk in frappe.get_all(
		"Packing",
		filters={"job__npd_number": ["in", pps], "is_deliverd": 0},
		fields=["name", "item", "packed_qty", "extra"],
		order_by="creation",
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


_DN_ROW_SKIP = {
	"name", "idx", "parent", "parentfield", "parenttype", "docstatus", "creation",
	"modified", "modified_by", "owner", "doctype", "__islocal", "__unsaved", "__onload",
}


@frappe.whitelist()
def get_delivery_items_from_packings(sales_order, packings):
	"""Build Delivery Note item rows from the selected Packings, pulling ALL item data
	(rate, uom, warehouse, so_detail, …) from the Sales Order via ERPNext's mapper. One row
	per packing (qty = packed_qty), each linked to its Packing. The client replaces the DN
	items with these, so unselected items drop off and selected qtys are updated."""
	import json as _json
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	if isinstance(packings, str):
		packings = _json.loads(packings or "[]")
	if not sales_order or not packings:
		return {"items": [], "customer": ""}

	dn = make_delivery_note(sales_order)
	so_item = {}
	for it in dn.items:
		so_item.setdefault(it.item_code, it)

	rows = []
	for pk in packings:
		d = frappe.db.get_value("Packing", pk, ["item", "packed_qty"], as_dict=True)
		if not d:
			continue
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
			row = {"item_code": d.item, "against_sales_order": sales_order}
			row.update({k: v for k, v in soi.items() if v not in (None, "") and k != "name"})
			if soi.get("name"):
				row["so_detail"] = soi["name"]
		row["qty"] = flt(d.packed_qty)
		row["against_sales_order"] = sales_order
		row["custom_packing"] = pk
		rows.append(row)
	return {"items": rows, "customer": dn.customer}


def mark_packings_delivered(doc, method=None):
	"""Delivery Note hook: flag the linked Packing records delivered on submit, and clear
	the flag on cancel (so their stock is released back to 'New' qty)."""
	delivered = 1 if method != "on_cancel" else 0
	for it in (doc.get("items") or []):
		pk = it.get("custom_packing")
		if pk and frappe.db.exists("Packing", pk):
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
	                  = current warehouse stock - qty on other undelivered Packing records.
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
		{"item": item, "is_deliverd": 0, "name": ["!=", current_packing or "__none__"]},
		field="packed_qty",
	)
	out["new_qty"] = max(available - earmarked, 0.0)
	return out
