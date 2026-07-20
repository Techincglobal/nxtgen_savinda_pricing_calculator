// Copyright (c) 2026, Techincglobal.com
// Adds "Create Cost Sheet" button to the Inquiry (Opportunity) form

frappe.ui.form.on("Opportunity", {
	refresh: function (frm) {
		if (frm.doc.docstatus >= 2) return;  // cancelled — no action

		// Savinda uses its own sales process (Cost Sheet → Savinda Quotation), NOT the
		// native ERPNext sales cycle. Remove the standard "Create" sales buttons that
		// ERPNext's own opportunity.js adds. This runs after core JS, so the buttons exist.
		_remove_standard_sales_buttons(frm);
		setTimeout(function () { _remove_standard_sales_buttons(frm); }, 300);

		frm.add_custom_button(__("Create NPD Ticket"), function () {
			_npd_create_checked("Opportunity", frm.doc.name);
		}, __("Pricing"));

		frm.add_custom_button(__("Create Job Ticket"), function () {
			frappe.call({
				method: "nxtgen_savinda_pricing_calculator.api.job_ticket.create_job_ticket_from_inquiry",
				args: { opportunity: frm.doc.name },
				freeze: true,
				freeze_message: __("Creating Job Ticket…"),
				callback: function (r) {
					if (r.message && r.message.job_ticket) {
						frappe.set_route("Form", "Job Ticket", r.message.job_ticket);
					}
				},
			});
		}, __("Pricing"));

		frm.add_custom_button(__("Create Cost Sheet"), function () {
			// Check if a cost sheet already exists for this inquiry
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Cost Sheet",
					filters: { inquiry: frm.doc.name },
					fields: ["name", "docstatus"],
					limit: 5,
				},
				callback: function (r) {
					var existing = r.message || [];
					if (existing.length) {
						// Show existing sheets — let user decide
						var list_html = existing.map(function (cs) {
							var status = cs.docstatus === 0 ? "Draft" : cs.docstatus === 1 ? "Submitted" : "Cancelled";
							return "<li><a href='/app/cost-sheet/" + cs.name + "' target='_blank'>" + cs.name + "</a> — " + status + "</li>";
						}).join("");
						frappe.confirm(
							"<b>" + existing.length + " Cost Sheet(s) already exist for this inquiry:</b><ul>" + list_html + "</ul>"
							+ "<br>Create a new one anyway?",
							function () {
								create_cost_sheet(frm);
							}
						);
					} else {
						create_cost_sheet(frm);
					}
				},
			});
		}, __("Pricing"));
	},
});

// Create an NPD ticket, validating materials first. Unlinked materials open a mapping
// table (sample material → pick actual Item) before creating.
function _npd_create_checked(source_type, source_name) {
	var M = "nxtgen_savinda_pricing_calculator.api.job_ticket.create_npd_checked";
	frappe.call({
		method: M, args: { source_type: source_type, source_name: source_name, force: 0 },
		freeze: true, freeze_message: __("Checking materials…"),
		callback: function (r) {
			if (!r.message) return;
			if (r.message.needs_confirm) {
				_npd_material_map_dialog(M, source_type, source_name, r.message.unlinked || []);
			} else if (r.message.job_ticket) {
				frappe.set_route("Form", "Job Ticket", r.message.job_ticket);
			}
		},
	});
}

function _npd_material_map_dialog(method, source_type, source_name, unlinked) {
	var controls = [];
	var d = new frappe.ui.Dialog({
		title: __("Link Materials to Items"),
		size: "large",
		fields: [
			{ fieldtype: "HTML", fieldname: "info", options:
				"<div style='margin-bottom:8px;color:#555;font-size:12px'>These materials are not linked to an Item. "
				+ "Pick the actual Item for each (leave blank to skip — it won't be added to the sample BOM), then Proceed.</div>" },
			{ fieldtype: "HTML", fieldname: "tbl" },
		],
		primary_action_label: __("Proceed & Create NPD"),
		primary_action: function () {
			var map = {};
			controls.forEach(function (c) { var v = c.ctrl.get_value(); if (v) map[c.name] = v; });
			d.hide();
			frappe.call({
				method: method,
				args: { source_type: source_type, source_name: source_name, force: 1, material_map: JSON.stringify(map) },
				freeze: true, freeze_message: __("Creating NPD…"),
				callback: function (r2) {
					if (r2.message && r2.message.job_ticket) frappe.set_route("Form", "Job Ticket", r2.message.job_ticket);
				},
			});
		},
	});
	var $w = d.fields_dict.tbl.$wrapper;
	$w.html("<table class='table table-bordered' style='font-size:12px;margin:0'>"
		+ "<thead><tr><th style='width:45%'>Sample Material</th><th>Actual Item</th></tr></thead><tbody></tbody></table>");
	var $tb = $w.find("tbody");
	(unlinked || []).forEach(function (name, i) {
		var $tr = $("<tr>").appendTo($tb);
		$("<td>").text(name).appendTo($tr);
		var $td = $("<td>").appendTo($tr);
		var ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Item", fieldname: "item_" + i, placeholder: __("Select Item") },
			parent: $td.get(0), render_input: true,
		});
		ctrl.set_value("");
		controls.push({ name: name, ctrl: ctrl });
	});
	d.show();
}

function _remove_standard_sales_buttons(frm) {
	// The normal-ERP-sales-process buttons under the "Create" group.
	["Quotation", "Customer", "Supplier Quotation", "Request For Quotation"].forEach(function (label) {
		try { frm.remove_custom_button(label, __("Create")); } catch (e) { /* not present */ }
	});
}

function create_cost_sheet(frm) {
	// Carry compliance rows from Inquiry to Cost Sheet
	// Compliance Details child DocType has one field: 'type' (Link → Compliance)
	var compliance_rows = (frm.doc.custom_compliance || [])
		.map(function (c) { return c.type || ""; })
		.filter(Boolean)
		.map(function (val) {
			return { doctype: "Compliance Details", type: val };
		});

	frappe.call({
		method: "frappe.client.insert",
		args: {
			doc: {
				doctype:       "Cost Sheet",
				inquiry:       frm.doc.name,
				subject:       frm.doc.custom_subject    || frm.doc.name,
				customer_name: frm.doc.customer_name     || "",
				colour:        frm.doc.custom_colour     || 0,
				item_group:    frm.doc.custom_item_group || "",
				tiep:          frm.doc.custom_tiep       || "",
				compliance:    compliance_rows,
			},
		},
		freeze: true,
		freeze_message: __("Creating Cost Sheet…"),
		callback: function (r) {
			if (r.message) {
				frappe.show_alert({ message: __("Cost Sheet created: ") + r.message.name, indicator: "green" });
				frappe.set_route("Form", "Cost Sheet", r.message.name);
			}
		},
	});
}
