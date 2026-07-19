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
