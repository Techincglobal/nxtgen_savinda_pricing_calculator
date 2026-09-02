// Copyright (c) 2026, Techincglobal.com and contributors
// Delivery Note — packing is the ONLY item source: fetch items from the packing list (undelivered
// Packing records for a Sales Order that has stock to dispatch), then save. The standard
// "Get Items From" options and manual row entry are removed.

var PK = "nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.packing.packing.";

frappe.ui.form.on("Delivery Note", {
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			// Packing is the only way to add items — drop the standard get-items options and
			// block manual rows so nothing can be added except via "Get from Packing".
			frm.remove_custom_button("Sales Order", "Get Items From");
			frm.remove_custom_button("Sales Invoice", "Get Items From");
			frm.set_df_property("items", "cannot_add_rows", 1);

			frm.add_custom_button(__("Get from Packing"), function () {
				_packing_popup(frm);
			}, __("Get Items From"));
		}
	},
});

function _default_so(frm) {
	var so = "";
	(frm.doc.items || []).some(function (r) {
		if (r.against_sales_order) { so = r.against_sales_order; return true; }
		return false;
	});
	return so;
}

// Load the dispatchable Sales Orders first, then open the dialog with the picker restricted
// to just those orders (only orders with undelivered packings — i.e. something to dispatch).
function _packing_popup(frm) {
	frappe.call({
		method: PK + "dispatchable_sales_orders",
		callback: function (r) { _open_packing_dialog(frm, r.message || []); },
	});
}

function _open_packing_dialog(frm, so_list) {
	var d = new frappe.ui.Dialog({
		title: __("Get Items from Packing"),
		size: "large",
		fields: [
			{ fieldtype: "Link", options: "Sales Order", fieldname: "sales_order",
				label: __("Sales Order (with stock to dispatch)"), reqd: 1, default: _default_so(frm),
				get_query: function () {
					return { filters: { name: ["in", (so_list && so_list.length) ? so_list : ["__none__"]] } };
				} },
			{ fieldtype: "Button", fieldname: "load", label: __("Load Available Packings") },
			{ fieldtype: "HTML", fieldname: "list" },
		],
		primary_action_label: __("Set Delivery Note Items"),
		primary_action: function () {
			var names = [];
			d.$wrapper.find(".pk-check:checked").each(function () {
				names.push($(this).attr("data-pk"));
			});
			if (!names.length) { frappe.msgprint(__("Select at least one packing.")); return; }
			var so = d.get_value("sales_order");
			frappe.call({
				method: PK + "get_delivery_items_from_packings",
				args: { sales_order: so, packings: JSON.stringify(names) },
				freeze: true, freeze_message: __("Fetching item data from the Sales Order…"),
				callback: function (r) {
					var m = r.message || {};
					var rows = m.items || [];
					if (!rows.length) { frappe.msgprint(__("No matching items on the Sales Order.")); return; }
					if (m.customer && !frm.doc.customer) frm.set_value("customer", m.customer);
					// Replace the items table: only the selected packings' items remain,
					// with qty from the packing and all other data from the Sales Order.
					frm.clear_table("items");
					rows.forEach(function (row) { frm.add_child("items", row); });
					frm.refresh_field("items");
					d.hide();
					frappe.show_alert({ message: __("Delivery Note set from {0} packing(s).", [rows.length]), indicator: "green" });
				},
			});
		},
	});

	function render(rows) {
		d.__packings = rows || [];
		if (!rows || !rows.length) {
			d.fields_dict.list.$wrapper.html(
				"<div style='color:#888;padding:8px'>" + __("No available (undelivered) packings for this Sales Order.") + "</div>");
			return;
		}
		var html = "<table class='table table-bordered' style='font-size:12px;margin-bottom:0'><thead><tr>"
			+ "<th style='width:34px;text-align:center'><input type='checkbox' class='pk-all' checked></th>"
			+ "<th>" + __("Description") + "</th>"
			+ "<th style='width:90px;text-align:right'>" + __("Quantity") + "</th></tr></thead><tbody>";
		rows.forEach(function (p) {
			html += "<tr>"
				+ "<td style='text-align:center'><input type='checkbox' class='pk-check' data-pk='"
				+ frappe.utils.escape_html(p.packing) + "' checked></td>"
				+ "<td><b>" + frappe.utils.escape_html(p.item_name) + "</b>"
				+ "<div style='color:#666'>Item code: " + frappe.utils.escape_html(p.item) + "</div>"
				+ (p.breakdown ? "<div style='margin-top:4px'>" + frappe.utils.escape_html(p.breakdown) + "</div>" : "")
				+ (p.extra ? "<div style='margin-top:4px'>Extra: " + p.extra + "</div>" : "")
				+ "</td>"
				+ "<td style='text-align:right'>" + format_number(p.packed_qty) + "</td>"
				+ "</tr>";
		});
		html += "</tbody></table>";
		d.fields_dict.list.$wrapper.html(html);
		d.$wrapper.find(".pk-all").on("change", function () {
			d.$wrapper.find(".pk-check").prop("checked", $(this).prop("checked"));
		});
	}

	function load() {
		var so = d.get_value("sales_order");
		if (!so) { frappe.msgprint(__("Select a Sales Order.")); return; }
		if (!frm.doc.customer) {
			frappe.db.get_value("Sales Order", so, "customer", function (v) {
				if (v && v.customer) frm.set_value("customer", v.customer);
			});
		}
		frappe.call({
			method: PK + "get_available_packings",
			args: { sales_order: so },
			callback: function (r) { render(r.message || []); },
		});
	}

	d.fields_dict.load.$input.on("click", load);
	d.show();
	if (d.get_value("sales_order")) load();
}
