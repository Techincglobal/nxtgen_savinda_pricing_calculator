// Copyright (c) 2026, Techincglobal.com
// Sales Order → BOM Builder + Create Production Plan (Job Ticket).

frappe.ui.form.on("Sales Order", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 0 || frm.doc.docstatus === 1) {
			frm.add_custom_button(__("BOM Builder"), function () {
				window.location.href = "/app/bom-builder?so=" + encodeURIComponent(frm.doc.name);
			}, __("Manufacturing"));

			frm.add_custom_button(__("Create Production Plan"), function () {
				if (frm.is_dirty()) { frappe.msgprint(__("Save the Sales Order before checking its Production Plans.")); return; }
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.create_plan_from_sales_order",
					args: { sales_order: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Production Plan…"),
					callback: function (r) {
						var result = r.message || {};
						if (result.already_planned) {
							var links = (result.existing_production_plans || []).map(function (name) {
								return '<a href="/app/production-plan/' + encodeURIComponent(name) + '">'
									+ frappe.utils.escape_html(name) + '</a>';
							}).join('<br>');
							frappe.msgprint({title: __("Production Plans Already Exist"),
								message: __("All Sales Order quantities are already planned.") + '<br>' + links, indicator: "blue"});
							if ((result.existing_production_plans || []).length === 1) frappe.set_route("Form", "Production Plan", result.production_plan);
							return;
						}
						if (result.production_plan) {
							if ((result.existing_production_plans || []).length) frappe.show_alert({
								message: __("Already planned quantities excluded. Opening the remaining items."), indicator: "blue"});
							frappe.set_route("Form", "Production Plan", result.production_plan);
						}
					},
				});
			}, __("Manufacturing"));
		}


		frm.set_query("custom_sales_person", function () {
			return {
				filters: {
					department: "Marketing - SGSPL",
					status: "Active"
				}
			};
		});
		frm.set_query("custom_cs_person", function () {
			return {
				filters: {
					department: "CS - SGSPL",
					status: "Active"
				}
			};
		});
		frm.remove_custom_button('Quotation', 'Get items from');
		// Delivery Notes are raised only from the Delivery Note's "Get from Packing" flow, so
		// drop the direct "Create > Delivery Note" option on the Sales Order.
		frm.remove_custom_button('Delivery Note', 'Create');
	},
});
