// Copyright (c) 2026, Techincglobal.com and contributors
// Pricing Item — Client Script

frappe.ui.form.on("Pricing Item", {

	refresh: function (frm) {
		calc_all_amounts(frm);
	},

	unit_price: function (frm) { calc_all_amounts(frm); },
	qty: function (frm) { calc_all_amounts(frm); },
	selling_unit_price: function (frm) { calc_all_amounts(frm); },
	sscl: function (frm) { calc_all_amounts(frm); },
	vat: function (frm) { calc_all_amounts(frm); },
});


// ── Cost Breakdown child table events ─────────────────────────
frappe.ui.form.on("Pricing Item Brackdown", {

	// When Calculation Breakdown is selected → auto-fill unit price
	calculation_brakedown: function (frm, cdt, cdn) {
		var row = locals[cdt][cdn];
		if (!row.calculation_brakedown) return;
		frappe.call({
			method: "frappe.client.get_value",
			args: {
				doctype: "Calculation Breakdown",
				filters: { name: row.calculation_brakedown },
				fieldname: ["unit_cost", "customer_name", "ref"],
			},
			callback: function (r) {
				if (!r.message) return;
				frappe.model.set_value(cdt, cdn, "unit__price", flt_v(r.message.unit_cost));
				if (r.message.ref && !row.discription)
					frappe.model.set_value(cdt, cdn, "discription", r.message.ref || "");
				calc_bd_row(cdt, cdn);
			},
		});
	},

	unit__price: function (frm, cdt, cdn) { calc_bd_row(cdt, cdn); },
	qty: function (frm, cdt, cdn) { calc_bd_row(cdt, cdn); },

	cost_brackdown_remove: function (frm) { calc_all_amounts(frm); },
});


// ─────────────────────────────────────────────────────────────
//  CALCULATE all amounts on the main form
// ─────────────────────────────────────────────────────────────

function calc_all_amounts(frm) {
	var qty = flt_v(frm.doc.qty);
	var unit = flt_v(frm.doc.unit_price);
	var sell_unit = flt_v(frm.doc.selling_unit_price);

	// Cost amount
	frappe.model.set_value(frm.doctype, frm.docname, "ammount", round2(qty * unit));

	// Selling amount with taxes
	var taxed = sell_unit;
	if (frm.doc.sscl) taxed = taxed * 1.025;
	if (frm.doc.vat) taxed = taxed * 1.18;
	frappe.model.set_value(frm.doctype, frm.docname, "selling_ammount", round2(qty * taxed));

	// Also recalc breakdown rows
	(frm.doc.cost_brackdown || []).forEach(function (row) {
		calc_bd_row(row.doctype, row.name);
	});
}


// ─────────────────────────────────────────────────────────────
//  CALCULATE one Pricing Item Breakdown row
// ─────────────────────────────────────────────────────────────

function calc_bd_row(cdt, cdn) {
	var row = locals[cdt][cdn];
	var amt = round2(flt_v(row.qty) * flt_v(row.unit__price));
	frappe.model.set_value(cdt, cdn, "amount", amt);
}


// ─────────────────────────────────────────────────────────────
//  UTILITIES
// ─────────────────────────────────────────────────────────────
function flt_v(v) { return parseFloat(v || 0) || 0; }
function round2(v) { return Math.round((flt_v(v) + Number.EPSILON) * 100) / 100; }