// Copyright (c) 2026, Techincglobal.com
frappe.ui.form.on("Job Ticket", {
	refresh: function (frm) {
		if (!frm.is_new() && frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Pull Data from Source"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.job_ticket.pull_source_data",
					args: { job_ticket: frm.doc.name },
					freeze: true, freeze_message: __("Pulling…"),
					callback: function (r) {
						if (r.message && r.message.ok) { frm.reload_doc(); }
						else if (r.message && r.message.msg) { frappe.msgprint(r.message.msg); }
					},
				});
			}, __("Actions"));
		}

		if (frm.is_new()) return;

		// NPD → create a zero-cost sample FG + BOM (for delivery note / invoice).
		if (frm.doc.ticket_type === "NPD") {
			frm.add_custom_button(__("Create Sample FG & BOM"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.job_ticket.create_sample_fg_and_bom",
					args: { job_ticket: frm.doc.name },
					freeze: true, freeze_message: __("Creating sample FG & BOM…"),
					callback: function (r) {
						if (!r.message) return;
						var m = r.message, lines = [];
						if ((m.fg || []).length) lines.push("<b>FG created:</b> " + m.fg.join(", "));
						if ((m.bom || []).length) lines.push("<b>BOM created:</b> " + m.bom.join(", "));
						if ((m.skipped_bom || []).length) lines.push("<b>No BOM</b> (exists / no linked materials): " + m.skipped_bom.join(", "));
						if ((m.unlinked || []).length) lines.push("<b>Unlinked materials skipped:</b> " + m.unlinked.join(", "));
						frappe.msgprint({ title: __("Sample FG & BOM"), message: lines.join("<br>") || __("Done"), indicator: "green" });
						frm.reload_doc();
					},
				});
			}, __("Create"));
		}

		// Create Production Plan — draft PP with FG + BOM + qty (Job AND NPD).
		if (!frm.doc.production_plan) {
			frm.add_custom_button(__("Create Production Plan"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.job_ticket.create_production_plan",
					args: { job_ticket: frm.doc.name },
					freeze: true, freeze_message: __("Creating Production Plan…"),
					callback: function (r) {
						if (!r.message) return;
						if (r.message.warning) {
							frappe.msgprint({ title: __("Some FGs skipped"), message: r.message.warning, indicator: "orange" });
						}
						if (r.message.production_plan) {
							frm.reload_doc();
							frappe.set_route("Form", "Production Plan", r.message.production_plan);
						}
					},
				});
			}, __("Create"));
		} else {
			frm.add_custom_button(__("Production Plan"), function () {
				frappe.set_route("Form", "Production Plan", frm.doc.production_plan);
			}, __("View"));
		}
	},

	// Selecting a source pulls its data (works for new + saved drafts).
	sales_order: function (frm) { _fetch_source(frm, "Sales Order", frm.doc.sales_order); },
	cost_sheet:  function (frm) { _fetch_source(frm, "Cost Sheet", frm.doc.cost_sheet); },
	inquiry:     function (frm) { _fetch_source(frm, "Opportunity", frm.doc.inquiry); },
});

function _fetch_source(frm, source_type, source_name) {
	if (!source_name || frm._jt_pulling) return;
	frm._jt_pulling = true;
	frappe.call({
		method: "nxtgen_savinda_pricing_calculator.api.job_ticket.get_source_data",
		args: { source_type: source_type, source_name: source_name },
		callback: function (r) {
			try { if (r.message) _apply_source(frm, r.message); }
			finally { frm._jt_pulling = false; }
		},
		error: function () { frm._jt_pulling = false; },
	});
}

function _apply_source(frm, data) {
	var h = data.header || {};
	Object.keys(h).forEach(function (f) {
		if (h[f] !== undefined && h[f] !== null && h[f] !== "") frm.set_value(f, h[f]);
	});
	frm.clear_table("items");
	(data.items || []).forEach(function (row) {
		var c = frm.add_child("items");
		Object.keys(row).forEach(function (k) { if (row[k] !== null && row[k] !== undefined) c[k] = row[k]; });
	});
	frm.clear_table("bom_materials");
	(data.bom_materials || []).forEach(function (row) {
		var c = frm.add_child("bom_materials");
		Object.keys(row).forEach(function (k) { if (row[k] !== null && row[k] !== undefined) c[k] = row[k]; });
	});
	frm.refresh_field("items");
	frm.refresh_field("bom_materials");
}
