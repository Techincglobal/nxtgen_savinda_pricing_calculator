// Copyright (c) 2026, Techincglobal.com
// Sales Order → BOM Builder + Create Production Plan (Job Ticket).

frappe.ui.form.on("Sales Order", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 0 || frm.doc.docstatus === 1) {
			frm.add_custom_button(__("BOM Builder"), function () {
				window.location.href = "/app/bom-builder?so=" + encodeURIComponent(frm.doc.name);
			}, __("Manufacturing"));

			frm.add_custom_button(__("Create Production Plan"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.create_plan_from_sales_order",
					args: { sales_order: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Production Plan…"),
					callback: function (r) {
						if (r.message && r.message.production_plan) {
							frappe.set_route("Form", "Production Plan", r.message.production_plan);
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
