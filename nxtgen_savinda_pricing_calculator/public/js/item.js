// FG Item actions.  Pricing must remain available after the quotation, because
// a customer can request a new quantity tier or a short-lived promotional rate.
frappe.ui.form.on("Item", {
	refresh: function (frm) {
		// Only costing-created finished goods use the guarded qty-pricing flow.
		if (frm.is_new() || !frm.doc.custom_cost_item || !window.nxtgen_pricing) return;

		frm.add_custom_button(__("Manage Qty Pricing Rules"), function () {
			nxtgen_pricing.showForFGs({
			doc: {
				currency: "LKR",
				conversion_rate: 1,
				customer: "",
				valid_till: null,
			},
		}, [{
			item_code: frm.doc.name,
			item_name: frm.doc.item_name,
			cost_item: frm.doc.custom_cost_item,
		}], function () { frm.reload_doc(); });
		});

		if (frappe.user.has_role("Supply Chain") || frappe.user.has_role("System Manager")) {
			frm.add_custom_button(__("Validate Artwork"), function () {
				var d = new frappe.ui.Dialog({
					title: __("Artwork Validation — {0}", [frm.doc.item_name]),
					fields: [
						{fieldtype: "Data", label: "Artwork No", default: frm.doc.custom_product_library || "", read_only: 1},
						{fieldtype: "Select", fieldname: "status", label: "Status", options: "Approved\nRejected", reqd: 1},
						{fieldtype: "Small Text", fieldname: "remarks", label: "Remarks"},
					],
					primary_action_label: __("Save Validation"),
					primary_action: function (v) {
						frappe.call({method: "nxtgen_savinda_pricing_calculator.api.artwork.validate_fg_artwork",
							args: {fg_item: frm.doc.name, status: v.status, remarks: v.remarks},
							callback: function () { d.hide(); frm.reload_doc(); }});
					},
				});
				d.show();
			});
		}
		},
});
