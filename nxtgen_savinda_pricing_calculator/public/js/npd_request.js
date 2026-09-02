// Copyright (c) 2026, Techincglobal.com
// NPD Request — sample flow: NPD Request (approval) → auto Material Request (Manufacture) →
// Production Plan. No Sales Order. On approval the Material Request is created automatically;
// from here you create the Production Plan for the standard Work Order pipeline.
frappe.ui.form.on("NPD Request", {
	refresh: function (frm) {
		if (frm.is_new()) { return; }

		if (frm.doc.material_request) {
			frm.add_custom_button(__("Material Request"), function () {
				frappe.set_route("Form", "Material Request", frm.doc.material_request);
			}, __("View"));
		}
		if (frm.doc.production_plan) {
			frm.add_custom_button(__("Production Plan"), function () {
				frappe.set_route("Form", "Production Plan", frm.doc.production_plan);
			}, __("View"));
		}
		if (frm.doc.cost_sheet) {
			frm.add_custom_button(__("Cost Sheet"), function () {
				frappe.set_route("Form", "Cost Sheet", frm.doc.cost_sheet);
			}, __("View"));
		}

		// Create the Production Plan once the request is approved (Material Request exists).
		if (frm.doc.material_request && !frm.doc.production_plan) {
			frm.add_custom_button(__("Create Production Plan"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.create_plan_from_npd_mr",
					args: { npd_request: frm.doc.name },
					freeze: true, freeze_message: __("Creating Production Plan…"),
					callback: function (r) {
						var m = r.message || {};
						if (!m.production_plan) { return; }
						frappe.msgprint({
							title: __("Production Plan Created"),
							message: __("Production Plan <a href='/app/production-plan/{0}'><b>{0}</b></a> created for the NPD sample.", [m.production_plan])
								+ (m.needs_bom ? "<br><span style='color:#b45309'>Some items have no BOM yet — open the BOM Builder from the plan.</span>" : ""),
							indicator: "green",
						});
						frm.reload_doc();
					},
				});
			}, __("Create")).addClass("btn-primary");
		}

		// Guidance banner by stage.
		if (!frm.doc.material_request) {
			frm.dashboard.set_headline(
				"Submit for NPD approval. On <b>Approve NPD</b> a Manufacture Material Request is created automatically; then use <b>Create → Create Production Plan</b>."
			);
		}
	},
});
