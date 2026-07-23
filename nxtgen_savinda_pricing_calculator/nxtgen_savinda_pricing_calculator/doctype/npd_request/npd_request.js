// Copyright (c) 2026, Techincglobal.com and contributors
// NPD Request — new-product request: create FG items, confirm BOM, create Production Plan.

frappe.ui.form.on("NPD Request", {
	refresh(frm) {
		if (frm.is_new()) return;

		var PP = "nxtgen_savinda_pricing_calculator.api.production_plan.";
		var DT = "nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.npd_request.npd_request.";

		// ── Artwork approval (same pattern as Cost Sheet) ──
		var roles = frappe.user_roles || [];
		var can_approve = roles.indexOf("Artwork Approver") >= 0 || roles.indexOf("System Manager") >= 0;
		var status = frm.doc.artwork_status || "Pending";
		var color = status === "Approved" ? "green" : (status === "Rejected" ? "red" : "orange");
		frm.dashboard.set_headline(
			'Artwork: <span class="indicator ' + color + '">' + status + "</span>"
			+ (frm.doc.artwork_approved_by ? " by " + frappe.utils.escape_html(frm.doc.artwork_approved_by) : "")
		);
		if (can_approve && status !== "Approved") {
			frm.add_custom_button(__("Approve Artwork"), function () {
				_artwork_action(frm, DT + "approve_artwork", "Approve Artwork");
			}, __("Artwork"));
		}
		if (can_approve && status !== "Rejected") {
			frm.add_custom_button(__("Reject Artwork"), function () {
				_artwork_action(frm, DT + "reject_artwork", "Reject Artwork");
			}, __("Artwork"));
		}

		// ── Actions ──
		// FG Items are created on the Cost Sheet (before the NPD); the BOM team creates/
		// confirms BOMs on the Production Plan (BOM Validation). The NPD stays light.

		// Open BOM Builder from the NPD's related Cost Sheet (BOM team creates BOMs here).
		if (frm.doc.cost_sheet) {
			frm.add_custom_button(__("Open BOM Builder"), function () {
				window.open(frappe.urllib.get_full_url("/app/bom-builder?cost_sheet=" + encodeURIComponent(frm.doc.cost_sheet)));
			}, __("Actions"));
		}

		if (!frm.doc.production_plan) {
			frm.add_custom_button(__("Create Production Plan"), function () {
				// BOMs are NOT required up-front — items without a BOM are fetched onto the
				// plan and flagged for the BOM team (Open BOM Builder → Sync BOMs there).
				frappe.call({
					method: PP + "create_plan_from_npd", args: { npd_request: frm.doc.name },
					freeze: true, freeze_message: __("Creating Production Plan…"),
					callback: function (r) {
						var m = r.message || {};
						if (m.production_plan) {
							if (m.needs_bom) {
								frappe.show_alert({ message: __("Plan created. Some items need a BOM — sent to the BOM team."), indicator: "orange" });
							}
							frappe.set_route("Form", "Production Plan", m.production_plan);
						}
					},
				});
			}, __("Create"));
		} else {
			frm.add_custom_button(__("View Production Plan"), function () {
				frappe.set_route("Form", "Production Plan", frm.doc.production_plan);
			}, __("Create"));
		}

		// ── Print / PDF (format by pricing type) ──
		frm.add_custom_button(__("Download PDF"), function () {
			var url = "/api/method/frappe.utils.print_format.download_pdf?doctype=NPD+Request&name="
				+ encodeURIComponent(frm.doc.name)
				+ "&format=NPD+Job+Ticket&no_letterhead=1";
			window.open(frappe.urllib.get_full_url(url));
		}, __("Actions"));
	},
});

function _artwork_action(frm, method, title) {
	var d = new frappe.ui.Dialog({
		title: __(title),
		fields: [{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") }],
		primary_action_label: __(title),
		primary_action: function (values) {
			frappe.call({
				method: method,
				args: { npd_request: frm.doc.name, remarks: values.remarks || "" },
				freeze: true,
				callback: function () { d.hide(); frm.reload_doc(); frappe.show_alert({ message: __("Done."), indicator: "green" }); },
			});
		},
	});
	d.show();
}
