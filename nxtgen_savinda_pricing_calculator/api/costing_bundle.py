"""One sellable assembly, retaining independently costed/manufactured components."""
import frappe
from frappe.utils import flt


def setup_bundle_fields():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
	visible = "eval:doc.custom_quote_as_bundle"
	create_custom_fields({
		"Savinda Quotation": [
			{"fieldname": "custom_combined_quote", "label": "Combined Item Quotation", "fieldtype": "Check", "read_only": 1, "insert_after": "cost_sheet"},
			{"fieldname": "custom_combined_specs", "label": "Combined Product — Customer-facing Specifications", "fieldtype": "Section Break", "insert_after": "items", "depends_on": "eval:doc.custom_combined_quote"},
			{"fieldname": "custom_combined_title", "label": "Complete Product Name", "fieldtype": "Data", "insert_after": "custom_combined_specs", "depends_on": "eval:doc.custom_combined_quote"},
			{"fieldname": "custom_combined_material", "label": "Printed In / Materials", "fieldtype": "Small Text", "insert_after": "custom_combined_title", "depends_on": "eval:doc.custom_combined_quote", "description": "Customer-facing material descriptions only. Review before issuing."},
			{"fieldname": "custom_combined_colours", "label": "Colours", "fieldtype": "Data", "insert_after": "custom_combined_material", "depends_on": "eval:doc.custom_combined_quote"},
			{"fieldname": "custom_combined_finishing", "label": "Combined Finishings", "fieldtype": "Small Text", "insert_after": "custom_combined_colours", "depends_on": "eval:doc.custom_combined_quote"},
			{"fieldname": "custom_combined_size", "label": "Complete Product / Component Sizes", "fieldtype": "Small Text", "insert_after": "custom_combined_finishing", "depends_on": "eval:doc.custom_combined_quote"},
			{"fieldname": "custom_combined_remarks", "label": "Combined Product Specifications / Remarks", "fieldtype": "Small Text", "insert_after": "custom_combined_size", "depends_on": "eval:doc.custom_combined_quote"},
			{"fieldname": "custom_combined_specs_end", "fieldtype": "Section Break", "insert_after": "custom_combined_remarks", "depends_on": "eval:doc.custom_combined_quote"},
		],
		"Cost Sheet": [
			{"fieldname": "custom_bundle_section", "label": "Quotation Bundle / Assembly", "fieldtype": "Section Break", "insert_after": "qty_breaks"},
			{"fieldname": "custom_quote_as_bundle", "label": "Quote as One Bundle", "fieldtype": "Check", "insert_after": "custom_bundle_section"},
			{"fieldname": "custom_bundle_name", "label": "Bundle Product Name", "fieldtype": "Data", "insert_after": "custom_quote_as_bundle", "depends_on": visible},
			{"fieldname": "custom_bundle_qty", "label": "Bundle Order Qty", "fieldtype": "Float", "insert_after": "custom_bundle_name", "depends_on": visible},
			{"fieldname": "custom_bundle_uom", "label": "Bundle Stock UOM", "fieldtype": "Link", "options": "UOM", "default": "Nos", "insert_after": "custom_bundle_qty", "depends_on": visible},
			{"fieldname": "custom_bundle_assembly_cost", "label": "Assembly / Packing Unit Cost (Base Currency)", "fieldtype": "Currency", "insert_after": "custom_bundle_uom", "depends_on": visible, "default": "0"},
			{"fieldname": "custom_bundle_assembly_price", "label": "Assembly / Packing Unit Selling Price (Base Currency)", "fieldtype": "Currency", "insert_after": "custom_bundle_assembly_cost", "depends_on": visible, "default": "0"},
			{"fieldname": "custom_bundle_fg", "label": "Assembled Bundle FG", "fieldtype": "Link", "options": "Item", "insert_after": "custom_bundle_assembly_price", "depends_on": visible, "allow_on_submit": 1, "read_only": 1},
			{"fieldname": "custom_bundle_components", "label": "Bundle Components", "fieldtype": "Table", "options": "Costing Bundle Component", "insert_after": "custom_bundle_fg", "depends_on": visible, "allow_on_submit": 1,
			 "description": "One bundle per Cost Sheet. Keep each component calculation separate; Qty per Bundle is the assembly ratio. After submission, only Component FG links can change."},
		],
		"Savinda Quotation Item": [{"fieldname": "custom_costing_bundle", "label": "Bundle Cost Sheet", "fieldtype": "Link", "options": "Cost Sheet", "insert_after": "cost_item", "read_only": 1}],
		"Item": [{"fieldname": "custom_costing_bundle", "label": "Bundle Cost Sheet", "fieldtype": "Link", "options": "Cost Sheet", "insert_after": "custom_cost_item", "read_only": 1}],
		"Product Library": [{"fieldname": "custom_costing_bundle", "label": "Bundle Cost Sheet", "fieldtype": "Link", "options": "Cost Sheet", "insert_after": "cost_item", "read_only": 1}],
	}, update=True)
	_ensure_combined_print_format()


def _ensure_combined_print_format(overwrite=False):
	"""Create the optional layout; explicit install/migration sync may update it."""
	name = "Savinda Combined Item Quotation"
	exists = frappe.db.exists("Print Format", name)
	if exists and not overwrite:
		return
	from pathlib import Path
	template = Path(frappe.get_app_path("nxtgen_savinda_pricing_calculator", "templates", "prints", "combined_item_quotation.html"))
	values = {"doc_type": "Savinda Quotation",
		"standard": "No", "custom_format": 1, "print_format_type": "Jinja", "html": template.read_text(encoding="utf-8"),
		"font_size": 10, "margin_top": 12, "margin_bottom": 12, "margin_left": 12, "margin_right": 12}
	if exists:
		doc = frappe.get_doc("Print Format", name)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({"doctype": "Print Format", "name": name, **values}).insert(ignore_permissions=True)


def combined_quote_defaults(cs):
	"""Snapshot safe customer specifications, never component prices or raw Item names."""
	from nxtgen_savinda_pricing_calculator.api.offset_calculator import resolve_common_material_name
	def distinct(values):
		out, seen = [], set()
		for value in values:
			value = str(value or "").strip()
			if value and value.casefold() not in seen:
				out.append(value)
				seen.add(value.casefold())
		return out
	materials, sizes, colours, remarks = [], [], [], []
	for part in cs.get("custom_bundle_components") or []:
		cb = frappe.get_doc("Calculation Breakdown", part.calculation_breakdown)
		ci = frappe.get_doc("cost Item", part.cost_item)
		materials.append(resolve_common_material_name(base_material=cb.get("base_material"), calculation_breakdown=cb.name))
		sizes.append(ci.get("dimensions") or cb.get("carton_size"))
		if flt(cb.get("no_of_colors")) > 0:
			colours.append(str(int(cb.no_of_colors)))
		remarks.append(ci.get("bom_remark"))
	return {"custom_combined_quote": 1, "custom_combined_title": cs.custom_bundle_name,
		"custom_combined_material": " / ".join(distinct(materials)),
		"custom_combined_size": "; ".join(distinct(sizes)),
		"custom_combined_colours": " / ".join(distinct(colours)),
		"custom_combined_finishing": ", ".join(bundle_finishings(cs)),
		"custom_combined_remarks": "\n".join(distinct(remarks))}


@frappe.whitelist()
def get_combined_quote_defaults(cost_sheet):
	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	cs.check_permission("read")
	if not cs.get("custom_quote_as_bundle"):
		frappe.throw("This Cost Sheet is not a combined-item quotation bundle.")
	return combined_quote_defaults(cs)


def validate_bundle(cs):
	if not cs.get("custom_quote_as_bundle"):
		return
	old = cs.get_doc_before_save()
	if old and old.docstatus == 1:
		keys = ("custom_quote_as_bundle", "custom_bundle_name", "custom_bundle_qty", "custom_bundle_uom", "custom_bundle_assembly_cost", "custom_bundle_assembly_price")
		definition = lambda doc: [(r.cost_item, flt(r.qty_per_bundle), r.calculation_breakdown) for r in doc.get("custom_bundle_components") or []]
		if any(cs.get(k) != old.get(k) for k in keys) or definition(cs) != definition(old):
			frappe.throw("Submitted bundle definitions are locked. Cancel/amend the Cost Sheet to change components or ratios.")
	if not (cs.get("custom_bundle_name") or "").strip() or flt(cs.get("custom_bundle_qty")) <= 0:
		frappe.throw("Enter a Bundle Product Name and a positive Bundle Order Qty.")
	if not cs.get("custom_bundle_uom"):
		frappe.throw("Select the Bundle Stock UOM.")
	if min(flt(cs.get("custom_bundle_assembly_cost")), flt(cs.get("custom_bundle_assembly_price"))) < 0:
		frappe.throw("Assembly/packing prices cannot be negative.")
	if any(q != int(q) for q in bundle_quantities(cs)):
		frappe.throw("Use whole bundle quantities for the order quantity and quantity breaks.")
	items = {r.item for r in cs.get("pricing_list") or [] if r.item}
	seen, calculations = set(), set()
	for row in cs.get("custom_bundle_components") or []:
		if row.cost_item not in items or row.cost_item in seen:
			frappe.throw("Every bundle component must be a distinct Cost Item from this Cost Sheet.")
		seen.add(row.cost_item)
		if row.calculation_breakdown in calculations:
			frappe.throw("Each bundle component needs an independent calculation; do not share a combined printing calculation.")
		calculations.add(row.calculation_breakdown)
		if flt(row.qty_per_bundle) <= 0:
			frappe.throw(f"Component {row.cost_item}: Qty per Bundle must be positive.")
		if not frappe.db.exists("Cost Item Calculation", {"parent": row.cost_item, "calculation_breakdown": row.calculation_breakdown}):
			frappe.throw(f"Select a calculation belonging to component {row.cost_item}.")
		if frappe.db.get_value("Calculation Breakdown", row.calculation_breakdown, "pricing_type") != cs.pricing_type:
			frappe.throw("Bundle components must use the Cost Sheet's production type. Mixed Offset/Flexo bundles are not supported.")
		if row.fg_item and frappe.db.get_value("Item", row.fg_item, "custom_cost_item") != row.cost_item:
			frappe.throw(f"Component FG {row.fg_item} must be linked to Cost Item {row.cost_item}.")
	if not items or seen != items:
		frappe.throw("Add every Cost Item from this Cost Sheet to Bundle Components exactly once.")
	if cs.get("custom_bundle_fg") and frappe.db.get_value("Item", cs.custom_bundle_fg, "custom_costing_bundle") != cs.name:
		frappe.throw("The assembled FG must be created for this bundle Cost Sheet.")


def bundle_quantities(cs):
	return sorted({flt(cs.custom_bundle_qty)} | {flt(r.qty) for r in cs.get("qty_breaks") or [] if flt(r.qty) > 0})


def bundle_finishings(cs):
	"""Distinct component finishings, keeping the first component/selection order."""
	from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils.jinja import get_cb_finishings
	out, seen = [], set()
	for component in cs.get("custom_bundle_components") or []:
		for name in get_cb_finishings(component.calculation_breakdown):
			name = (name or "").strip()
			key = name.casefold()
			if name and key not in seen:
				seen.add(key)
				out.append(name)
	return out


def price_bundle(cs, qty):
	"""Recalculate at component qty = bundle qty × ratio; never merge sheet layouts."""
	from nxtgen_savinda_pricing_calculator.api.offset_calculator import calculate_qty_break
	if not cs.get("custom_quote_as_bundle"):
		frappe.throw("This Cost Sheet is not configured as a quotation bundle.")
	validate_bundle(cs)
	if flt(qty) <= 0:
		frappe.throw("Bundle quantity must be positive.")
	cost, sell = flt(cs.custom_bundle_assembly_cost), flt(cs.custom_bundle_assembly_price)
	margins, components = [], []
	for row in cs.custom_bundle_components:
		ratio = flt(row.qty_per_bundle)
		result = calculate_qty_break(row.calculation_breakdown, flt(qty) * ratio) or {}
		if result.get("error") or flt(result.get("sell_unit")) <= 0:
			frappe.throw(f"Cannot price bundle component {row.cost_item}: {result.get('error') or 'no positive selling price'}. Fix its calculation first.")
		cost += flt(result.get("unit_cost")) * ratio
		sell += flt(result.get("sell_unit")) * ratio
		margins.append(flt(result.get("profit_margin")))
		components.append({"cost_item": row.cost_item, "qty": flt(qty) * ratio, "unit_cost": result.get("unit_cost"), "sell_unit": result.get("sell_unit")})
	# Conservative existing workflow, also accounting for any loss on assembly charges.
	if cs.custom_bundle_assembly_cost or cs.custom_bundle_assembly_price:
		assembly_price = flt(cs.custom_bundle_assembly_price)
		margins.append((assembly_price - flt(cs.custom_bundle_assembly_cost)) / assembly_price * 100 if assembly_price else -100)
	return {"qty": flt(qty), "unit_cost": round(cost, 6), "rate": round(sell, 6), "profit_margin": min(margins), "components": components}


def quote_rows(cs, currency, conversion_rate):
	finishings = " + ".join(bundle_finishings(cs))
	return [{"item_name": cs.custom_bundle_name, "custom_costing_bundle": cs.name,
		"finishing": finishings,
		"finish_good": cs.custom_bundle_fg or "", "finishing_variant": 1, "qty": result["qty"],
		"unit_cost": result["unit_cost"], "base_selling_price": result["rate"],
		"selling_price": result["rate"] / (flt(conversion_rate) or 1), "currency": currency}
		for result in (price_bundle(cs, qty) for qty in bundle_quantities(cs))]


@frappe.whitelist()
def get_quote_rows(cost_sheet, currency=None, conversion_rate=1):
	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	cs.check_permission("read")
	return quote_rows(cs, currency, conversion_rate)


@frappe.whitelist()
def component_defaults(cost_sheet):
	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	cs.check_permission("read")
	rows = []
	for row in cs.pricing_list:
		if not row.item or any(r["cost_item"] == row.item for r in rows):
			continue
		cb = frappe.db.get_value("Cost Item Calculation", {"parent": row.item}, "calculation_breakdown", order_by="idx asc")
		fgs = frappe.get_all("Item", filters={"custom_cost_item": row.item, "has_variants": 0}, pluck="name")
		item_name = frappe.db.get_value("cost Item", row.item, "cost_item_name") or row.get("item_name") or ""
		rows.append({"cost_item": row.item, "cost_item_name": item_name, "qty_per_bundle": 1, "calculation_breakdown": cb or "", "fg_item": fgs[0] if len(fgs) == 1 else ""})
	return rows


@frappe.whitelist()
def sync_component_fgs(cost_sheet):
	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	cs.check_permission("write")
	validate_bundle(cs)
	defaults = {r["cost_item"]: r["fg_item"] for r in component_defaults(cost_sheet)}
	for row in cs.custom_bundle_components:
		if not row.fg_item:
			row.fg_item = defaults.get(row.cost_item) or ""
	cs.save()
	return {"missing": [r.cost_item for r in cs.custom_bundle_components if not r.fg_item]}


@frappe.whitelist()
def create_bundle_fg(cost_sheet, item_group, department, customer=None):
	from nxtgen_savinda_pricing_calculator.api.manufacturing import create_fg_item
	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	cs.check_permission("write")
	# Serialize parent creation so double-clicks cannot produce two assembled FGs.
	frappe.db.sql("select name from `tabCost Sheet` where name=%s for update", cs.name)
	cs.reload()
	frappe.has_permission("Item", "create", throw=True)
	if cs.docstatus != 1:
		frappe.throw("Submit the bundle Cost Sheet before creating its assembled FG.")
	validate_bundle(cs)
	if not cs.get("custom_quote_as_bundle"):
		frappe.throw("Enable Quote as One Bundle on the Cost Sheet first.")
	if cs.custom_bundle_fg:
		frappe.throw("This bundle already has an assembled FG. Open it instead of creating another.")
	if not customer and cs.inquiry:
		opp = frappe.db.get_value("Opportunity", cs.inquiry, ["opportunity_from", "party_name"], as_dict=True)
		if opp and opp.opportunity_from == "Customer":
			customer = opp.party_name
	if not customer and cs.customer_name:
		customer = frappe.db.get_value("Customer", {"customer_name": cs.customer_name}, "name")
	result = create_fg_item(cs.custom_bundle_name, cs.custom_bundle_name, item_group, department,
		stock_uom=cs.custom_bundle_uom, costing_bundle=cs.name,
		pl_overrides={"department": cs.pricing_type, "customer": customer,
			"finishings": [{"process_name": name} for name in bundle_finishings(cs)]})
	cs.custom_bundle_fg = result["item_code"]
	cs.save()
	for row in frappe.get_all("Savinda Quotation Item", filters={"custom_costing_bundle": cs.name}, fields=["name", "finish_good"]):
		if not row.finish_good:
			frappe.db.set_value("Savinda Quotation Item", row.name, "finish_good", result["item_code"])
	return result


def pricing_tiers(cost_sheet):
	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	# Assigned FG reviewers can inspect the linked pricing summary without granting
	# them edit access to all Cost Sheets. Other callers need ordinary costing access.
	from nxtgen_savinda_pricing_calculator.api.fg_approval import _can_review, _settings
	if not _can_review(_settings()):
		cs.check_permission("read")
	rows = [price_bundle(cs, qty) for qty in bundle_quantities(cs)]
	for i, row in enumerate(rows):
		row["min_qty"] = 1 if i == 0 else row["qty"]
		row["max_qty"] = rows[i + 1]["qty"] - 1 if i + 1 < len(rows) else 0
	return rows


def builder_components(cs, bundle_qty=None):
	validate_bundle(cs)
	if any(not row.fg_item for row in cs.custom_bundle_components):
		frappe.throw("Create component FGs and use Sync Component FG Links on the Cost Sheet before opening the bundle BOM Builder.")
	return [{"item_code": row.fg_item, "item_name": frappe.db.get_value("Item", row.fg_item, "item_name") or row.fg_item,
		"qty": flt(cs.custom_bundle_qty if bundle_qty is None else bundle_qty) * flt(row.qty_per_bundle), "cost_item": row.cost_item,
		"calculation_breakdown": row.calculation_breakdown} for row in cs.custom_bundle_components if row.fg_item]


def validate_assembly_bom(cs, bom_name):
	"""The sales bundle ratios and the manufacturing assembly must agree."""
	validate_bundle(cs)
	if not bom_name or any(not r.fg_item for r in cs.custom_bundle_components):
		frappe.throw("Create/link all component FGs and a submitted assembly BOM for the bundle first.")
	bom = frappe.get_doc("BOM", bom_name)
	if bom.docstatus != 1 or not bom.is_active or bom.item != cs.custom_bundle_fg:
		frappe.throw("Select a submitted active assembly BOM for the assembled bundle FG.")
	actual = {}
	for row in bom.items:
		factor = flt(row.conversion_factor) or 1
		actual[row.item_code] = actual.get(row.item_code, 0) + flt(row.qty) * factor / (flt(bom.quantity) or 1)
	for row in cs.custom_bundle_components:
		if abs(actual.get(row.fg_item, 0) - flt(row.qty_per_bundle)) > 0.000001:
			frappe.throw(f"Assembly BOM quantity for {row.fg_item} must match Qty per Bundle ({row.qty_per_bundle}) on {cs.name}.")
