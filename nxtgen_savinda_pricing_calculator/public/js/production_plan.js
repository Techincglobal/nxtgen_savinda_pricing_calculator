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
