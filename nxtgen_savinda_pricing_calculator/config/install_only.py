"""Export the approved Savinda configuration for a one-time app installation.

Run this against the reviewed source site, commit the two JSON files it writes,
then install the app on the target site.  The installer reads these files only
from ``after_install`` and creates records only when they do not already exist.
It never runs during ``bench migrate`` or an app update.
"""

import json
import os

import frappe


# Product Library, Cost Item, Cost Sheet, Calculation Breakdown and all
# transactional documents are intentionally absent from this list.
MASTER_DOCTYPES = (
	"Cost Fact",
	"Offset Machine",
	"Offset Ink",
	"Flexo Foil",
	"Offset Spec",
	"Costing Configuration",
)

WORKSPACES = ("CEO", "BOM Desk", "Production", "Customer Service")
PRINT_FORMATS = (
	"Job Ticket",
	"Offset Job Ticket",
	"Flexo Job Ticket",
	"NPD Job Ticket",
	"Offset NPD Job Ticket",
	"Flexo NPD Job Ticket",
	"Savinda Quotation",
	"Cost Sheet Summary",
)
ROLES = ("Artwork Approver", "CS Team", "BOM Team", "Pre-Print Team", "Supply Chain", "Quotation Approver", "NPD Approver")
WORKFLOWS = ("Production Plan Approval",)
INQUIRY_DOCTYPES = ("Inquiry", "Opportunity")

_TRANSIENT_KEYS = {
	"__islocal", "__onload", "_user_tags", "_comments", "_assign", "_liked_by",
	"creation", "modified", "owner", "modified_by", "idx", "docstatus",
}


def export_from_site():
	"""Write reviewed install-only JSON snapshots into this app.

	This is deliberately an explicit developer command, not an install hook. It
	does not modify database records. Review and commit the written JSON files
	before deploying the app.
	"""
	masters, missing, source_documents = _export_doctypes(MASTER_DOCTYPES)
	item_groups, items, missing_items = _export_calculation_items(source_documents)
	masters = item_groups + items + masters
	missing.extend(missing_items)
	ui = []
	ui.extend(_export_named("Role", ROLES, missing))
	ui.extend(_export_named("Workflow", WORKFLOWS, missing))
	ui.extend(_export_named("Workspace", WORKSPACES, missing))
	ui.extend(_export_named("Print Format", PRINT_FORMATS, missing))
	ui.extend(_export_inquiry_scripts("Client Script", missing))
	ui.extend(_export_inquiry_scripts("Server Script", missing))

	directory = frappe.get_app_path("nxtgen_savinda_pricing_calculator", "fixtures", "install_only")
	os.makedirs(directory, exist_ok=True)
	_write(os.path.join(directory, "master_data.json"), masters)
	_write(os.path.join(directory, "user_interface.json"), ui)

	return {
		"master_records": len(masters),
		"ui_records": len(ui),
		"missing_tables": missing,
		"files": [
			os.path.join(directory, "master_data.json"),
			os.path.join(directory, "user_interface.json"),
		],
	}


def _export_doctypes(doctypes):
	records, missing, source_documents = [], [], []
	for doctype in doctypes:
		meta = frappe.get_meta(doctype)
		# Singles are stored in tabSingles rather than a tab<DocType> table.
		if meta.issingle:
			document = frappe.get_doc(doctype, doctype).as_dict()
			source_documents.append(document)
			records.append(_clean(document))
			continue
		if not frappe.db.table_exists(doctype):
			missing.append(doctype)
			continue
		# Parent Item Groups must be inserted before their children on the target.
		order_by = "lft asc" if doctype == "Item Group" else "name asc"
		for name in frappe.get_all(doctype, pluck="name", order_by=order_by, limit_page_length=0):
			document = frappe.get_doc(doctype, name).as_dict()
			source_documents.append(document)
			records.append(_clean(document))
	return records, missing, source_documents


def _export_calculation_items(source_documents):
	"""Export only Items linked by the approved calculator configuration.

	The source site has thousands of ordinary ERP items.  We traverse Link-to-Item
	fields on Cost Facts, Inks, Specs and Costing Configuration (including child
	tables), then export precisely those Item documents and their Item Group tree.
	"""
	item_names = set()
	for document in source_documents:
		_collect_item_links(document, item_names)

	missing = []
	items = []
	groups = set()
	for name in sorted(item_names):
		if not frappe.db.exists("Item", name):
			missing.append("Item: " + name)
			continue
		document = frappe.get_doc("Item", name).as_dict()
		items.append(_clean(document))
		if document.get("item_group"):
			groups.update(_item_group_ancestors(document["item_group"]))

	item_groups = []
	if groups:
		for name in frappe.get_all("Item Group", filters={"name": ["in", groups]}, pluck="name", order_by="lft asc", limit_page_length=0):
			item_groups.append(_document("Item Group", name))
	return item_groups, items, missing


def _collect_item_links(document, item_names):
	if not isinstance(document, dict) or not document.get("doctype"):
		return
	meta = frappe.get_meta(document["doctype"])
	for field in meta.fields:
		value = document.get(field.fieldname)
		if field.fieldtype == "Link" and field.options == "Item" and value:
			item_names.add(value)
		elif field.fieldtype in ("Table", "Table MultiSelect") and isinstance(value, list):
			for child in value:
				_collect_item_links(child, item_names)


def _item_group_ancestors(name):
	groups = set()
	while name and name not in groups and frappe.db.exists("Item Group", name):
		groups.add(name)
		name = frappe.db.get_value("Item Group", name, "parent_item_group")
	return groups


def _export_named(doctype, names, missing):
	if not frappe.db.table_exists(doctype):
		missing.append(doctype)
		return []
	return [_document(doctype, name) for name in names if frappe.db.exists(doctype, name)]


def _export_inquiry_scripts(doctype, missing):
	if not frappe.db.table_exists(doctype):
		missing.append(doctype)
		return []
	meta = frappe.get_meta(doctype)
	field = "dt" if meta.has_field("dt") else "reference_doctype" if meta.has_field("reference_doctype") else None
	if not field:
		return []
	names = frappe.get_all(doctype, filters={field: ["in", INQUIRY_DOCTYPES]}, pluck="name", limit_page_length=0)
	return [_document(doctype, name) for name in names]


def _document(doctype, name):
	document = _clean(frappe.get_doc(doctype, name).as_dict())
	# These are site-owned templates, not framework-standard print formats. This
	# prevents a later code sync from treating them as standard assets.
	if doctype == "Print Format":
		document["standard"] = "No"
	return document


def _clean(value):
	if isinstance(value, list):
		return [_clean(item) for item in value]
	if not isinstance(value, dict):
		return value
	return {key: _clean(item) for key, item in value.items() if key not in _TRANSIENT_KEYS}


def _write(path, records):
	with open(path, "w", encoding="utf-8") as handle:
		# Frappe documents may contain Python date/datetime values.  Their ISO
		# string representation is the fixture format Frappe expects on import.
		json.dump(records, handle, indent=2, ensure_ascii=False, default=str)
		handle.write("\n")
