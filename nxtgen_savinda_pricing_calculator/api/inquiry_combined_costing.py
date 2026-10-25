"""Opt-in inquiry component import; the existing Create Breakdown flow is unchanged."""
import math

import frappe
from frappe.utils import cint, flt


def _positive(value, label):
	value = flt(value)
	if not math.isfinite(value) or value <= 0:
		frappe.throw(f"{label} must be positive.")
	return value


def inquiry_components(opp, source):
	"""Join finishings by stable row key, never by a component's display name."""
	if source not in ("Gift Box", "Book"):
		frappe.throw("Select Gift Box or Book as the component source.")
	book = source == "Book"
	rows = opp.get("custom_pages" if book else "custom_gift_box_types") or []
	finishings = opp.get("custom_page_finishings" if book else "custom_gift_box_finishings") or []
	book_qty = flt(opp.get("custom_item_qty")) or sum(
		flt(row.get("qty")) for row in opp.get("custom_breakdown") or []
	)
	parts = []
	for row in rows:
		keys = {k for k in (row.get("row_key"), row.get("name")) if k}
		selected, seen = [], set()
		for finishing in finishings:
			name = (finishing.get("finishing") or finishing.get("finishing_operation") or "").strip()
			if finishing.get("page_row_name") in keys and cint(finishing.get("checked")) and name and name not in seen:
				selected.append(name)
				seen.add(name)
		name = (row.get("page_type") if book else row.get("type")) or f"Component {row.idx}"
		pages = _positive(row.get("no_of_pages"), f"{name}: Number of pages") if book else 0
		qty = _positive(book_qty * pages if book else row.get("quantity"), f"{name}: Component quantity")
		parts.append({
			"key": row.name, "name": name.strip(), "qty": qty, "no_of_pages": pages,
			"colour": cint(row.get("colour")) if book else cint(opp.get("custom_colour")),
			"material": row.get("material") if book else "",
			"base_material": row.get("custom_item") if not book else "",
			"dimensions": row.get("dimensions") or opp.get("custom_dimensions") or "",
			"remarks": row.get("remarks") or "", "finishings": selected,
		})
	if not parts:
		frappe.throw(f"This Inquiry has no {source} components.")
	return parts


@frappe.whitelist()
def preview(cost_sheet, source="Gift Box"):
	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	cs.check_permission("read")
	if not cs.inquiry:
		frappe.throw("Link an Inquiry first.")
	opp = frappe.get_doc("Opportunity", cs.inquiry)
	opp.check_permission("read")
	parts = inquiry_components(opp, source)
	return {"components": parts}


@frappe.whitelist()
def create(cost_sheet, source, component_keys):
	"""Populate pricing items only; users create calculations through the normal dialog."""
	cs = frappe.get_doc("Cost Sheet", cost_sheet, for_update=True)
	cs.check_permission("write")
	if cs.docstatus != 0 or cs.get("pricing_list") or cs.get("custom_bundle_components") or cs.get("custom_quote_as_bundle"):
		frappe.throw("Use an empty draft Cost Sheet. Existing costing is never replaced by this action.")
	if not cs.inquiry:
		frappe.throw("Link an Inquiry first.")
	frappe.has_permission("cost Item", "create", throw=True)
	opp = frappe.get_doc("Opportunity", cs.inquiry)
	opp.check_permission("read")
	parts = {p["key"]: p for p in inquiry_components(opp, source)}
	keys = frappe.parse_json(component_keys)
	if not isinstance(keys, list) or not keys or any(not isinstance(k, str) for k in keys):
		frappe.throw("Select at least one component.")
	if len(set(keys)) != len(keys) or any(k not in parts for k in keys):
		frappe.throw("Invalid or duplicate component selection. Reload the preview.")
	created = []
	for key in keys:
		part = parts[key]
		base = part["base_material"]
		if not base and part["material"]:
			base = frappe.db.get_value("Boards and Papers", part["material"], "item") or ""
		ci = frappe.get_doc({"doctype": "cost Item", "inquiry": opp.name, "subject": cs.subject,
			"cost_item_name": f"{cs.subject or opp.get('custom_subject') or opp.name} - {part['name']}", "page_type": part["name"],
			"item_qty": part["qty"], "no_of_pages": part["no_of_pages"], "colour": part["colour"],
			"material": part["material"] or base, "dimensions": part["dimensions"], "bom_remark": part["remarks"],
			"inquiry_component_key": f"{source}:{key}",
			"inquiry_finishings": [{"finishing": name} for name in part["finishings"]]}).insert()
		cs.append("pricing_list", {"item": ci.name, "item_name": ci.cost_item_name,
			"qty": part["qty"], "dimensions": part["dimensions"], "bom_remark": part["remarks"],
			"inquiry_finishings": ", ".join(part["finishings"])})
		created.append({"cost_item": ci.name})
	cs.save()
	return {"cost_sheet": cs.name, "created": created}
