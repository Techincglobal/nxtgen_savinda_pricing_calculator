// Copyright (c) 2026, Techincglobal.com
// Production Plan — "Add Wastage" to raw material rows.
// BOMs are built net (no wastage); wastage is layered in here at planning level.

frappe.ui.form.on("Production Plan", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 0 && (frm.doc.mr_items || []).length) {
			frm.add_custom_button(__("Add Wastage"), function () {
				_show_wastage_dialog(frm);
			}, __("Actions"));
		}

		// Fetch/refresh the Job Ticket header + line specs from the source (SO / NPD).
		if (!frm.is_new() && (frm.doc.po_items || []).length) {
			frm.add_custom_button(__("Fetch Job Ticket Details"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.fetch_ticket_details",
					args: { production_plan: frm.doc.name },
					freeze: true, freeze_message: __("Fetching…"),
					callback: function () {
						frappe.show_alert({ message: __("Job Ticket details fetched."), indicator: "green" });
						frm.reload_doc();
					},
				});
			}, __("Actions"));
		}

		// BOM team helpers — for ticket plans still awaiting BOMs (draft only).
		if (!frm.is_new() && frm.doc.docstatus === 0 && frm.doc.custom_ticket_type) {
			if (frm.doc.custom_needs_bom) {
				frm.dashboard.set_headline(
					'<span class="indicator orange">Some items have no BOM</span> — Open BOM Builder to create them, then Sync BOMs.'
				);
			}
			frm.add_custom_button(__("Open BOM Builder"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.bom_builder_url",
					args: { production_plan: frm.doc.name },
					callback: function (r) {
						if (r.message) { window.open(r.message); }
						else { frappe.msgprint(__("No source (Sales Order / Cost Sheet) available to open the BOM Builder.")); }
					},
				});
			}, __("Actions"));

			frm.add_custom_button(__("Sync BOMs"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.sync_boms",
					args: { production_plan: frm.doc.name },
					freeze: true, freeze_message: __("Syncing BOMs…"),
					callback: function (r) {
						var m = r.message || {};
						frappe.msgprint({
							title: __("Sync BOMs"),
							message: __("Resolved: {0}<br>Still without BOM: {1}",
								[(m.resolved || []).join(", ") || "—", (m.still_missing || []).join(", ") || "—"]),
							indicator: (m.still_missing || []).length ? "orange" : "green",
						});
						frm.reload_doc();
					},
				});
			}, __("Actions"));
		}

		// Procurement — create a Purchase Request (Material Request) from raw materials.
		if (!frm.is_new() && frm.doc.docstatus === 0 && frm.doc.custom_ticket_type
			&& (frm.doc.mr_items || []).length) {
			frm.add_custom_button(__("Create Purchase Request"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.create_purchase_request",
					args: { production_plan: frm.doc.name },
					freeze: true, freeze_message: __("Creating Purchase Request…"),
					callback: function (r) {
						var m = r.message || {};
						if (m.material_request) {
							frappe.msgprint({
								title: __("Purchase Request"),
								message: __("Created: <a href='/app/material-request/{0}' target='_blank'>{0}</a>", [m.material_request]),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			}, __("Actions"));
		}

		// Job Ticket PDF — only for plans created through the ticket flow.
		if (!frm.is_new() && frm.doc.custom_ticket_type) {
			frm.add_custom_button(__("Job Ticket PDF"), function () {
				var url = "/api/method/frappe.utils.print_format.download_pdf?doctype=Production+Plan&name="
					+ encodeURIComponent(frm.doc.name)
					+ "&format=Job+Ticket&no_letterhead=1";
				window.open(frappe.urllib.get_full_url(url));
			}, __("Actions"));
		}
	},
});

function _show_wastage_dialog(frm) {
	var rows = (frm.doc.mr_items || []).filter(function (r) { return r.item_code; });
	if (!rows.length) {
		frappe.msgprint(__("No raw materials yet. Click 'Get Raw Materials for Production' first."));
		return;
	}

	// Default tolerance % from the costing (offset wastage %)
	frappe.db.get_single_value("Costing Configuration", "offset_wastage_pct").then(function (defpct) {
		defpct = parseFloat(defpct) || 0;

		var d = new frappe.ui.Dialog({
			title: __("Add Wastage to Raw Materials"),
			size: "large",
			fields: [
				{
					fieldtype: "Float", fieldname: "tolerance", label: "Tolerance %",
					default: defpct,
					description: "Default from costing wastage. Change it to recompute every row — you can still edit each wastage qty manually.",
				},
				{ fieldtype: "HTML", fieldname: "tbl" },
			],
			primary_action_label: __("Add Wastage to Rows"),
			primary_action: function () {
				var $w = d.fields_dict.tbl.$wrapper;
				var applied = 0;
				$w.find("input.waste-inp").each(function () {
					var idx = parseInt($(this).attr("data-idx"), 10);
					var w   = parseFloat($(this).val()) || 0;
					var row = frm.doc.mr_items[idx];
					if (!row) return;
					// Idempotent: net = current qty minus any wastage already applied
					var net = (parseFloat(row.quantity) || 0) - (parseFloat(row.custom_wastage_qty) || 0);
					row.custom_wastage_qty = w;      // stored separately (for print formats)
					row.quantity = net + w;           // total = net + wastage
					if (w > 0) applied++;
				});
				d.hide();
				frm.refresh_field("mr_items");
				frm.dirty();
				frappe.show_alert({ message: applied + " material row(s) updated with wastage.", indicator: "green" });
			},
		});

		function render(pct) {
			pct = parseFloat(pct) || 0;
			var html = '<div style="max-height:52vh;overflow:auto"><table class="table table-bordered" style="font-size:12px;margin:0">'
				+ '<thead><tr><th>Item</th><th class="text-right">Net Qty</th><th>UOM</th>'
				+ '<th class="text-right">Wastage Qty (editable)</th></tr></thead><tbody>';
			rows.forEach(function (r) {
				var idx = frm.doc.mr_items.indexOf(r);
				// Net = current qty minus wastage already applied (so it's stable on re-open)
				var net = (parseFloat(r.quantity) || 0) - (parseFloat(r.custom_wastage_qty) || 0);
				var w   = Math.round(net * (pct / 100) * 10000) / 10000;
				html += '<tr>'
					+ '<td>' + frappe.utils.escape_html(r.item_name || r.item_code) + '</td>'
					+ '<td class="text-right">' + net + '</td>'
					+ '<td>' + (r.uom || "") + '</td>'
					+ '<td class="text-right"><input type="number" step="0.0001" min="0" class="form-control input-sm waste-inp" '
					+ 'data-idx="' + idx + '" value="' + w + '" style="width:120px;display:inline-block"></td>'
					+ '</tr>';
			});
			html += '</tbody></table></div>';
			d.fields_dict.tbl.$wrapper.html(html);
		}

		d.show();
		render(defpct);
		// Recompute all rows when the tolerance % changes
		d.fields_dict.tolerance.$input.on("input change", function () {
			render(d.get_value("tolerance"));
		});
	});
}
