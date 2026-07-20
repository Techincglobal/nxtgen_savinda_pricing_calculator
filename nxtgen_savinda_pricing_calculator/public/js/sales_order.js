// Copyright (c) 2026, Techincglobal.com
// Adds BOM Builder button to ERPNext Sales Order form

frappe.ui.form.on("Sales Order", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 0 || frm.doc.docstatus === 1) {
			frm.add_custom_button(__("BOM Builder"), function () {
				window.location.href = "/app/bom-builder?so=" + encodeURIComponent(frm.doc.name);
			}, __("Manufacturing"));

			frm.add_custom_button(__("Create Job Ticket"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.job_ticket.create_job_ticket_from_sales_order",
					args: { sales_order: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Job Ticket…"),
					callback: function (r) {
						if (r.message && r.message.job_ticket) {
							frappe.set_route("Form", "Job Ticket", r.message.job_ticket);
						}
					},
				});
			}, __("Manufacturing"));

			frm.add_custom_button(__("Create NPD Ticket"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.job_ticket.create_npd_from_sales_order",
					args: { sales_order: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating NPD Ticket…"),
					callback: function (r) {
						if (r.message && r.message.job_ticket) {
							frappe.set_route("Form", "Job Ticket", r.message.job_ticket);
						}
					},
				});
			}, __("Manufacturing"));
		}
	},
});
