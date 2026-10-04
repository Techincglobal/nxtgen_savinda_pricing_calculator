// Copyright (c) 2026, Techincglobal.com and contributors
// For license information, please see license.txt

frappe.ui.form.on("Product Library", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.approval_required) return;
		const api = "nxtgen_savinda_pricing_calculator.api.fg_approval.";
		const status = frm.doc.approval_status;
		frm.dashboard.set_headline_alert(
			status === "Approved" ? __("FG approved and enabled.") : __("FG is disabled until Product Library and pricing are approved."),
			status === "Approved" ? "green" : "orange"
		);
		function review(action, remarks) {
			const run = () => frappe.call({
				method: api + "review_fg", args: { product_library: frm.doc.name, action, remarks },
				freeze: true, callback: () => frm.reload_doc()
			});
			if (frm.is_dirty()) return frm.save().then(run);
			return run();
		}
		frappe.call({
			method: api + "get_review_actions", args: { product_library: frm.doc.name }, callback(r) {
				const actions = r.message || {};
				if (actions.review && window.nxtgen_pricing) {
					frm.add_custom_button(__("Manage Pricing Rules"), () => {
						const open = () => nxtgen_pricing.showForFGs({
							doc: {
								customer: frm.doc.customer, currency: frappe.defaults.get_default("currency") || "LKR", conversion_rate: 1
							}
						}, [{ item_code: frm.doc.fg_item, item_name: frm.doc.product_name, cost_item: frm.doc.cost_item }], () => { });
						if (frm.is_dirty()) frm.save().then(open); else open();
					});
				}
				if (status === "Pending Validation") {
					if (actions.approve) frm.add_custom_button(__("Approve & Enable FG"), () => {
						frappe.confirm(__("Confirm Product Library details and pricing are correct, and enable this FG?"), () => review("approve"));
					});
					// if (actions.review) frm.add_custom_button(__("Return for Correction"), () => {
					// 	frappe.prompt({ fieldname: "remarks", fieldtype: "Small Text", label: __("Required Corrections"), reqd: 1 },
					// 		values => review("return", values.remarks), __("Return FG for Correction"));
					// });
				}
				// else if (status === "Returned for Correction" && actions.resend) {
				// 	frm.add_custom_button(__("Resend for Validation"), () => review("resend"));
				// }
			}
		});
	}
});
