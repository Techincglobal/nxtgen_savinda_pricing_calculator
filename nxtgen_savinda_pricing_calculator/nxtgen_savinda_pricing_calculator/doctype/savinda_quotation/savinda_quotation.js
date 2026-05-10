// Copyright (c) 2026, Techincglobal.com and contributors
// Savinda Quotation — Client Script

var API_CALC_QTY = "nxtgen_savinda_pricing_calculator.api.offset_calculator.calculate_qty_break";

frappe.ui.form.on("Savinda Quotation", {

	// ── On load ───────────────────────────────────────────────
	refresh: function (frm) {

		// Auto-load items when quotation has a cost_sheet but no items yet
		// This handles the case where quotation was just created from Cost Sheet
		if (!frm.is_new() && frm.doc.cost_sheet && !(frm.doc.items && frm.doc.items.length)) {
			frappe.show_alert({ message: "Loading items from Cost Sheet…", indicator: "blue" });
			_do_load_from_cost_sheet(frm, frm.doc.cost_sheet, function (loaded) {
				if (loaded) {
					frm.save();
					frappe.show_alert({ message: loaded + " item(s) loaded from Cost Sheet.", indicator: "green" });
				}
			});
		}

		// Fill from Cost Sheet button (always available when cost_sheet is set)
		if (frm.doc.cost_sheet) {
			frm.add_custom_button(__("Reload from Cost Sheet"), function () {
				frappe.confirm(
					"This will replace all current items with items from the linked Cost Sheet. Continue?",
					function () {
						frm.clear_table("items");
						_do_load_from_cost_sheet(frm, frm.doc.cost_sheet, function (loaded) {
							frm.refresh_field("items");
							if (loaded) frappe.show_alert({ message: loaded + " item(s) loaded.", indicator: "green" });
						});
					}
				);
			}, __("Fill"));
		} else if (frm.is_new()) {
			frm.add_custom_button(__("From Cost Sheet"), function () {
				_show_load_dialog(frm);
			}, __("Fill"));
		}

		// Calculate Qty Breaks button
		frm.add_custom_button(__("Calculate Qty Breaks"), function () {
			calculate_all_qty_breaks(frm);
		});

		// Add Qty Break Row button
		frm.add_custom_button(__("Add Qty Break"), function () {
			show_add_qty_break_dialog(frm);
		});

		// Print Quotation button
		if (!frm.is_new()) {
			frm.add_custom_button(__("Print Quotation"), function () {
				frappe.set_route("print", "Savinda Quotation", frm.doc.name, "Savinda Quotation Format");
			}, __("Actions"));
		}
	},

	// ── Auto-fill when inquiry selected ──────────────────────
	inquiry: function (frm) {
		if (!frm.doc.inquiry) return;
		frappe.call({
			method: "frappe.client.get",
			args: { doctype: "Opportunity", name: frm.doc.inquiry },
			callback: function (r) {
				if (!r.message) return;
				var opp = r.message;
				if (!frm.doc.customer && opp.customer_name)
					frm.set_value("customer", opp.customer_name);
			},
		});
	},

	// ── Auto-fill when cost_sheet selected ───────────────────
	cost_sheet: function (frm) {
		if (!frm.doc.cost_sheet) return;
		frappe.call({
			method: "frappe.client.get",
			args: { doctype: "Cost Sheet", name: frm.doc.cost_sheet },
			callback: function (r) {
				if (!r.message) return;
				var cs = r.message;
				if (!frm.doc.inquiry && cs.inquiry)
					frm.set_value("inquiry", cs.inquiry);
				if (!frm.doc.customer && cs.customer_name)
					frm.set_value("customer", cs.customer_name);
			},
		});
	},
});


// ── Child table events ────────────────────────────────────────
frappe.ui.form.on("Savinda Quotation Item", {
	selling_price: function (frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "is_manually_set", 1);
	},
});


// ─────────────────────────────────────────────────────────────
//  SHOW LOAD DIALOG (for new docs without a cost_sheet set)
// ─────────────────────────────────────────────────────────────

function _show_load_dialog(frm) {
	var d = new frappe.ui.Dialog({
		title: "Load Items from Cost Sheet",
		fields: [
			{
				fieldtype: "Link", fieldname: "cost_sheet",
				label: "Cost Sheet", options: "Cost Sheet", reqd: 1,
			},
		],
		primary_action_label: "Load",
		primary_action: function (vals) {
			d.hide();
			frm.set_value("cost_sheet", vals.cost_sheet);
			_do_load_from_cost_sheet(frm, vals.cost_sheet, function (loaded) {
				frm.refresh_field("items");
				if (loaded) frappe.show_alert({ message: loaded + " item(s) loaded.", indicator: "green" });
			});
		},
	});
	d.show();
}


// ─────────────────────────────────────────────────────────────
//  CORE LOAD FUNCTION — reads Cost Sheet pricing_list rows
//  and populates quotation items table sequentially
// ─────────────────────────────────────────────────────────────

function _do_load_from_cost_sheet(frm, cost_sheet_name, on_done) {
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "Cost Sheet", name: cost_sheet_name },
		callback: function (r) {
			if (!r.message) {
				frappe.msgprint({ message: "Could not load Cost Sheet: " + cost_sheet_name, indicator: "red" });
				return;
			}
			var cs = r.message;

			// Update header fields from cost sheet
			if (!frm.doc.inquiry && cs.inquiry)
				frm.set_value("inquiry", cs.inquiry);
			if (!frm.doc.customer && cs.customer_name)
				frm.set_value("customer", cs.customer_name);

			// Cost Sheet uses "pricing_list" as the child table fieldname
			var rows = cs.pricing_list || [];
			if (!rows.length) {
				frappe.msgprint({ message: "No items found in Cost Sheet pricing list.", indicator: "orange" });
				if (typeof on_done === "function") on_done(0);
				return;
			}

			var loaded = 0;

			function load_next(idx) {
				if (idx >= rows.length) {
					frm.refresh_field("items");
					if (typeof on_done === "function") on_done(loaded);
					return;
				}
				var row = rows[idx];

				// Skip rows with no item linked
				if (!row.item) {
					load_next(idx + 1);
					return;
				}

				// Fetch Cost Item details to get name, material, and calculation breakdown
				frappe.call({
					method: "frappe.client.get",
					args: { doctype: "cost Item", name: row.item },
					callback: function (r2) {
						var ci = r2.message || {};

						// Get first calculation breakdown from cost item
						var cb_name = "";
						if (ci.calculations && ci.calculations.length > 0) {
							cb_name = ci.calculations[0].calculation_breakdown || "";
						}

						// Add row to quotation items table
						var qrow = frm.add_child("items");
						qrow.item_name = ci.cost_item_name || row.item_name || row.item;
						qrow.cost_item = row.item;
						qrow.calculation_breakdown = cb_name;
						qrow.size = _format_size(ci);
						qrow.material = ci.material || "";
						qrow.finishing = ci.breakdown || "";
						qrow.finishing_variant = 1;
						qrow.qty = flt_v(row.qty);
						qrow.unit_cost = flt_v(row.unit_price);
						// Use selling_unit_price if available, else fall back to unit_price
						qrow.selling_price = flt_v(row.selling_unit_price) > 0
							? flt_v(row.selling_unit_price)
							: flt_v(row.unit_price);
						qrow.is_manually_set = 0;

						loaded++;
						load_next(idx + 1);
					},
					error: function () {
						// If cost item fetch fails, still add a basic row
						var qrow = frm.add_child("items");
						qrow.item_name = row.item_name || row.item;
						qrow.cost_item = row.item;
						qrow.qty = flt_v(row.qty);
						qrow.unit_cost = flt_v(row.unit_price);
						qrow.selling_price = flt_v(row.selling_unit_price) || flt_v(row.unit_price);
						qrow.finishing_variant = 1;
						loaded++;
						load_next(idx + 1);
					},
				});
			}
			load_next(0);
		},
	});
}

function _format_size(ci) {
	// Try to create a size string from available data
	// e.g. "93 x 28.5 mm" or just blank
	if (ci.page_type) return ci.page_type;
	return "";
}


// ─────────────────────────────────────────────────────────────
//  ADD QTY BREAK DIALOG
// ─────────────────────────────────────────────────────────────

function show_add_qty_break_dialog(frm) {
	var existing_items = (frm.doc.items || []).filter(function (r) {
		return r.item_name && r.calculation_breakdown;
	});

	if (!existing_items.length) {
		frappe.msgprint({ message: "No items with a Calculation Breakdown found. Load from Cost Sheet first.", indicator: "orange" });
		return;
	}

	// Build unique item/variant options
	var item_options = [];
	var seen = {};
	existing_items.forEach(function (r) {
		var key = r.item_name + "|" + (r.finishing_variant || 1);
		if (!seen[key]) {
			seen[key] = true;
			var label = r.item_name
				+ (r.finishing ? " (" + r.finishing + ")" : "")
				+ " — Variant " + (r.finishing_variant || 1);
			item_options.push({
				label: label,
				value: r.calculation_breakdown + "|" + (r.finishing_variant || 1) + "|" + r.item_name,
			});
		}
	});

	var d = new frappe.ui.Dialog({
		title: "Add Qty Break",
		fields: [
			{
				fieldtype: "Select",
				fieldname: "source_item",
				label: "Item / Variant",
				options: item_options.map(function (o) { return o.label + "\n" + o.value; }).join("\n"),
				reqd: 1,
			},
			{
				fieldtype: "Float",
				fieldname: "qty",
				label: "Qty Break",
				reqd: 1,
				description: "e.g. 5000, 10000, 25000",
			},
			{
				fieldtype: "Float",
				fieldname: "profit_margin",
				label: "Profit Margin % (optional)",
				description: "Leave blank to use the base calculation margin",
			},
		],
		primary_action_label: "Add & Calculate",
		primary_action: function (vals) {
			d.hide();

			// Find the matching option by label to get the value
			var selected_label = vals.source_item;
			var matched = item_options.find(function (o) {
				return o.label === selected_label || o.value === selected_label;
			});
			var raw = matched ? matched.value : selected_label;
			var parts = (raw || "").split("|");
			var cb_name = parts[0];
			var variant = parseInt(parts[1]) || 1;
			var iname = parts[2] || "";
			var qty = flt_v(vals.qty);

			if (!cb_name || !qty) {
				frappe.msgprint({ message: "Please select an item and enter a quantity.", indicator: "orange" });
				return;
			}

			var src = existing_items.find(function (r) {
				return r.calculation_breakdown === cb_name
					&& (r.finishing_variant || 1) === variant;
			});

			frappe.show_alert({ message: "Calculating for qty " + qty.toLocaleString() + "…", indicator: "blue" });

			frappe.call({
				method: API_CALC_QTY,
				args: {
					calculation_breakdown: cb_name,
					qty: qty,
					profit_margin: vals.profit_margin || null,
				},
				callback: function (r) {
					if (r.message && r.message.error) {
						frappe.msgprint({ message: "Calculation error: " + r.message.error, indicator: "red" });
						return;
					}
					var res = r.message || {};
					var qrow = frm.add_child("items");
					qrow.item_name = src ? src.item_name : iname;
					qrow.cost_item = src ? src.cost_item : "";
					qrow.calculation_breakdown = cb_name;
					qrow.size = src ? (src.size || "") : "";
					qrow.material = src ? (src.material || "") : "";
					qrow.finishing = src ? (src.finishing || "") : "";
					qrow.finishing_variant = variant;
					qrow.qty = qty;
					qrow.unit_cost = flt_v(res.unit_cost);
					qrow.selling_price = flt_v(res.sell_unit);
					qrow.is_manually_set = 0;

					frm.refresh_field("items");
					frappe.show_alert({
						message: "Added: " + qty.toLocaleString() + " pcs @ LKR " + flt_v(res.sell_unit).toFixed(4),
						indicator: "green",
					});
				},
			});
		},
	});
	d.show();
}


// ─────────────────────────────────────────────────────────────
//  CALCULATE ALL QTY BREAKS
// ─────────────────────────────────────────────────────────────

function calculate_all_qty_breaks(frm) {
	var rows = (frm.doc.items || []).filter(function (r) {
		return r.calculation_breakdown && r.qty && !r.is_manually_set;
	});

	if (!rows.length) {
		frappe.msgprint({ message: "No rows to calculate.", indicator: "orange" });
		return;
	}

	frappe.show_alert({ message: "Calculating " + rows.length + " row(s)…", indicator: "blue" });
	var updated = 0;

	function calc_next(idx) {
		if (idx >= rows.length) {
			frm.refresh_field("items");
			frappe.show_alert({ message: updated + " price(s) updated.", indicator: "green" });
			return;
		}
		var row = rows[idx];
		frappe.call({
			method: API_CALC_QTY,
			args: {
				calculation_breakdown: row.calculation_breakdown,
				qty: row.qty,
			},
			callback: function (r) {
				if (r.message && !r.message.error) {
					frappe.model.set_value(row.doctype, row.name, "unit_cost", flt_v(r.message.unit_cost));
					frappe.model.set_value(row.doctype, row.name, "selling_price", flt_v(r.message.sell_unit));
					updated++;
				}
				calc_next(idx + 1);
			},
		});
	}
	calc_next(0);
}


// ─────────────────────────────────────────────────────────────
//  UTILITIES
// ─────────────────────────────────────────────────────────────

function flt_v(v) { return parseFloat(v || 0) || 0; }