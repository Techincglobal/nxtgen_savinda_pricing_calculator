// Copyright (c) 2026, Techincglobal.com and contributors
// For license information, please see license.txt

frappe.ui.form.on("Packing", {
	refresh(frm) {
		// Item can only be one of the items in the selected Production Plan (Job/NPD).
		frm.set_query("item", function () {
			var items = frm.__pp_items || [];
			return { filters: { name: ["in", items.length ? items : [""]] } };
		});
		// Fetched/computed figures are read-only.
		["order_qty", "delivered_qty", "new_qty"].forEach(function (f) {
			frm.set_df_property(f, "read_only", 1);
		});
		if (frm.doc.job__npd_number && !frm.__pp_items) _load_pp_items(frm);
		if (frm.doc.job__npd_number && frm.doc.item) {
			frm.add_custom_button(__("Refresh Quantities"), function () {
				_fetch_packing_qtys(frm);
			});
		}
	},
	job__npd_number: function (frm) {
		frm.__pp_items = null;
		if (frm.doc.item) frm.set_value("item", null);
		if (frm.doc.job__npd_number) _load_pp_items(frm);
		_fetch_packing_qtys(frm);
	},
	item: function (frm) {
		_fetch_packing_qtys(frm);
	},
	type: function (frm) {
		_fetch_packing_qtys(frm);
	},
	packed_qty: function (frm) {
		calculate_total(frm);
	},

	extra: function (frm) {
		calculate_total(frm);
	}
});

// Fetch Order / Delivered / New qty from the Production Plan source (SO / NPD),
// Delivery Notes and current warehouse stock.
function _fetch_packing_qtys(frm) {
	if (!frm.doc.job__npd_number || !frm.doc.item) return;
	frappe.call({
		method: "nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.packing.packing.get_packing_quantities",
		args: {
			production_plan: frm.doc.job__npd_number,
			item: frm.doc.item,
			packing_type: frm.doc.type || null,
			current_packing: frm.is_new() ? null : frm.doc.name,
		},
		callback: function (r) {
			if (!r.message) return;
			frm.set_value("order_qty", r.message.order_qty);
			frm.set_value("delivered_qty", r.message.delivered_qty);
			frm.set_value("new_qty", r.message.new_qty);
		},
	});
}

// Load the Production Plan's item codes so the Item field can be restricted to them.
function _load_pp_items(frm) {
	if (!frm.doc.job__npd_number) return;
	frappe.call({
		method: "nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.packing.packing.get_pp_items",
		args: { production_plan: frm.doc.job__npd_number },
		callback: function (r) {
			frm.__pp_items = r.message || [];
			frm.refresh_field("item");
			// Default the Type (Job/NPD) from the Production Plan when not set.
			if (!frm.doc.type) {
				frappe.db.get_value("Production Plan", frm.doc.job__npd_number, "custom_ticket_type",
					function (v) { if (v && v.custom_ticket_type) frm.set_value("type", v.custom_ticket_type); });
			}
		},
	});
}

frappe.ui.form.on("Packing Details", {
	no_of_boxes: function (frm, cdt, cdn) {
		calculate_amount(frm, cdt, cdn);
	},

	pcs_per_box: function (frm, cdt, cdn) {
		calculate_amount(frm, cdt, cdn);
	},

	total: function (frm) {
		calculate_total(frm);
	},

	packages_remove: function (frm) {
		calculate_total(frm);
	}
});
function calculate_amount(frm, cdt, cdn) {
	let row = locals[cdt][cdn];

	row.total = flt(row.no_of_boxes) * flt(row.pcs_per_box);

	frm.refresh_field("packages"); // child table fieldname

	calculate_total(frm);
}

function calculate_total(frm) {
	let total = 0;

	(frm.doc.packages || []).forEach(row => {
		total += flt(row.total);
	});

	frm.set_value("packed_qty", total);
	frm.set_value("total", frm.doc.packed_qty + flt(frm.doc.extra));
}
