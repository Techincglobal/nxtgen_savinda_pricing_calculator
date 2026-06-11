// Copyright (c) 2026, Techincglobal.com
// Adds BOM Builder button to ERPNext Sales Order form

frappe.ui.form.on("Sales Order", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 0 || frm.doc.docstatus === 1) {
			frm.add_custom_button(__("BOM Builder"), function () {
				window.location.href = "/app/bom-builder?so=" + encodeURIComponent(frm.doc.name);
			}, __("Manufacturing"));
		}
	},
});
