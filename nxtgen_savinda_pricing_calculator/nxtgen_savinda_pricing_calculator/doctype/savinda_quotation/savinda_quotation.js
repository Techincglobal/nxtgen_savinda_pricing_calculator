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

		// ── Status colour indicator ───────────────────────────
		var status_colour = {
			"Draft":     "grey",
			"Sent":      "blue",
			"Accepted":  "green",
			"Won":       "darkgreen",
			"Lost":      "red",
			"Cancelled": "grey",
		};
		frm.set_indicator_formatter("status", function (doc) {
			return status_colour[doc.status] || "grey";
		});
		if (frm.doc.status) {
			frm.page.set_indicator(frm.doc.status, status_colour[frm.doc.status] || "grey");
		}

		// ── Won / Lost actions (available from any active status) ──────────
		var active = ["Draft", "Sent", "Accepted"];
		if (!frm.is_new() && active.indexOf(frm.doc.status) > -1) {
			frm.add_custom_button(__("Mark as Won"), function () {
				frappe.confirm(
					"Mark quotation <b>" + frm.doc.name + "</b> as <b style='color:#166534'>Won</b>?",
					function () {
						frappe.call({
							method: "frappe.client.set_value",
							args:   { doctype: "Savinda Quotation", name: frm.doc.name, fieldname: "status", value: "Won" },
							callback: function () {
								frm.reload_doc();
								frappe.show_alert({ message: "Quotation marked as Won ✓", indicator: "green" });
							},
						});
					}
				);
			}, __("Actions"));

			frm.add_custom_button(__("Mark as Lost"), function () {
				frappe.confirm(
					"Mark quotation <b>" + frm.doc.name + "</b> as <b style='color:#dc2626'>Lost</b>?",
					function () {
						frappe.call({
							method: "frappe.client.set_value",
							args:   { doctype: "Savinda Quotation", name: frm.doc.name, fieldname: "status", value: "Lost" },
							callback: function () {
								frm.reload_doc();
								frappe.show_alert({ message: "Quotation marked as Lost.", indicator: "red" });
							},
						});
					}
				);
			}, __("Actions"));
		}

		// ── Manufacturing functions (enabled when Won) ─────────────────────
		if (!frm.is_new() && frm.doc.status === "Won") {
			frm.add_custom_button(__("Create / Link FG Items"), function () {
				_show_create_fg_dialog(frm);
			}, __("Manufacturing"));

			frm.add_custom_button(__("Create Sales Order"), function () {
				_show_create_so_dialog(frm);
			}, __("Manufacturing"));

			frm.add_custom_button(__("BOM Builder"), function () {
				window.location.href = "/app/bom-builder?quotation=" + encodeURIComponent(frm.doc.name);
			}, __("Manufacturing"));
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
//  and populates quotation items table sequentially.
//
//  If the Cost Sheet has qty_breaks defined (e.g. 5000 / 20000 / 100000),
//  each cost item produces one quotation row per qty break (pricing
//  recalculated via calculate_qty_break). Otherwise one row per item
//  at the base quantity is added (original behaviour).
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

			var cs_rows = cs.pricing_list || [];
			if (!cs_rows.length) {
				frappe.msgprint({ message: "No items found in Cost Sheet pricing list.", indicator: "orange" });
				if (typeof on_done === "function") on_done(0);
				return;
			}

			// Qty breaks defined on the cost sheet (sorted ascending)
			var qty_breaks = (cs.qty_breaks || [])
				.map(function (b) { return { qty: flt_v(b.qty), label: b.label || "" }; })
				.filter(function (b) { return b.qty > 0; })
				.sort(function (a, b) { return a.qty - b.qty; });

			var loaded = 0;

			// Adds a single quotation row using base cost sheet data (no recalculation)
			function _add_base_row(ci, cs_row, qty_override, variant) {
				var qrow = frm.add_child("items");
				qrow.item_name        = ci.cost_item_name || cs_row.item_name || cs_row.item;
				qrow.cost_item        = cs_row.item;
				qrow.calculation_breakdown = (ci.calculations && ci.calculations[0])
					? (ci.calculations[0].calculation_breakdown || "") : "";
				qrow.size             = _format_size(ci);
				qrow.material         = ci.material || "";
				qrow.finishing        = ci.breakdown || "";
				qrow.finishing_variant = variant || 1;
				qrow.qty              = qty_override !== undefined ? qty_override : flt_v(cs_row.qty);
				qrow.unit_cost        = flt_v(cs_row.unit_price);
				qrow.selling_price    = flt_v(cs_row.selling_unit_price) > 0
					? flt_v(cs_row.selling_unit_price)
					: flt_v(cs_row.unit_price);
				qrow.is_manually_set  = 0;
				loaded++;
			}

			// Process one cost sheet row (cs_row_idx) then move to the next
			function process_item(cs_row_idx) {
				if (cs_row_idx >= cs_rows.length) {
					frm.refresh_field("items");
					if (typeof on_done === "function") on_done(loaded);
					return;
				}
				var cs_row = cs_rows[cs_row_idx];
				if (!cs_row.item) {
					process_item(cs_row_idx + 1);
					return;
				}

				frappe.call({
					method: "frappe.client.get",
					args: { doctype: "cost Item", name: cs_row.item },
					callback: function (r2) {
						var ci = r2.message || {};
						var cb_name = (ci.calculations && ci.calculations.length)
							? (ci.calculations[0].calculation_breakdown || "") : "";

						// No qty breaks or no CB to recalculate against → original single-row behaviour
						if (!qty_breaks.length || !cb_name) {
							_add_base_row(ci, cs_row, undefined, 1);
							process_item(cs_row_idx + 1);
							return;
						}

						// ── Qty-break mode ──────────────────────────────────────
						// One row per qty break, recalculated via calculate_qty_break.
						// finishing_variant stays 1 so all rows for this item group
						// together on the printed quotation.
						var base_info = {
							item_name:  ci.cost_item_name || cs_row.item_name || cs_row.item,
							cost_item:  cs_row.item,
							cb_name:    cb_name,
							size:       _format_size(ci),
							material:   ci.material || "",
							finishing:  ci.breakdown || "",
						};

						function add_break_row(qb_idx) {
							if (qb_idx >= qty_breaks.length) {
								process_item(cs_row_idx + 1);
								return;
							}
							var qb = qty_breaks[qb_idx];

							frappe.call({
								method: API_CALC_QTY,
								args: { calculation_breakdown: cb_name, qty: qb.qty },
								callback: function (r3) {
									var qrow = frm.add_child("items");
									qrow.item_name         = base_info.item_name;
									qrow.cost_item         = base_info.cost_item;
									qrow.calculation_breakdown = base_info.cb_name;
									qrow.size              = base_info.size;
									qrow.material          = base_info.material;
									qrow.finishing         = base_info.finishing;
									qrow.finishing_variant = 1;
									qrow.qty               = qb.qty;
									qrow.is_manually_set   = 0;

									if (r3.message && !r3.message.error) {
										qrow.unit_cost     = flt_v(r3.message.unit_cost);
										qrow.selling_price = flt_v(r3.message.sell_unit);
									} else {
										// Fallback if CB has no ui_state yet
										qrow.unit_cost     = flt_v(cs_row.unit_price);
										qrow.selling_price = flt_v(cs_row.selling_unit_price) || flt_v(cs_row.unit_price);
									}

									loaded++;
									add_break_row(qb_idx + 1);
								},
								error: function () {
									// Network / server error — fall back to base price
									var qrow = frm.add_child("items");
									qrow.item_name         = base_info.item_name;
									qrow.cost_item         = base_info.cost_item;
									qrow.calculation_breakdown = base_info.cb_name;
									qrow.size              = base_info.size;
									qrow.material          = base_info.material;
									qrow.finishing         = base_info.finishing;
									qrow.finishing_variant = 1;
									qrow.qty               = qb.qty;
									qrow.unit_cost         = flt_v(cs_row.unit_price);
									qrow.selling_price     = flt_v(cs_row.selling_unit_price) || flt_v(cs_row.unit_price);
									qrow.is_manually_set   = 0;
									loaded++;
									add_break_row(qb_idx + 1);
								},
							});
						}
						add_break_row(0);
					},
					error: function () {
						// Cost Item fetch failed — add one fallback row
						var qrow = frm.add_child("items");
						qrow.item_name         = cs_row.item_name || cs_row.item;
						qrow.cost_item         = cs_row.item;
						qrow.qty               = flt_v(cs_row.qty);
						qrow.unit_cost         = flt_v(cs_row.unit_price);
						qrow.selling_price     = flt_v(cs_row.selling_unit_price) || flt_v(cs_row.unit_price);
						qrow.finishing_variant = 1;
						loaded++;
						process_item(cs_row_idx + 1);
					},
				});
			}
			process_item(0);
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


// ─────────────────────────────────────────────────────────────
//  CREATE / LINK FG ITEMS
//  Groups quotation rows by cost_item; creates FG ERPNext Item
//  for each unique cost_item that has no finish_good yet.
// ─────────────────────────────────────────────────────────────

function _show_create_fg_dialog(frm) {
	// Build unique-per-cost_item list (skip rows that already have finish_good)
	var seen = {}, pending = [], already_done = 0;
	(frm.doc.items || []).forEach(function (row) {
		var key = row.cost_item || row.item_name;
		if (!seen[key]) {
			seen[key] = true;
			if (row.finish_good) {
				already_done++;
			} else {
				pending.push(row);
			}
		}
	});

	if (!pending.length) {
		frappe.msgprint({ title: "FG Items", message: "All items already have FG items linked (" + already_done + " linked).", indicator: "green" });
		return;
	}

	_create_fg_for_item(frm, pending, 0);
}

function _create_fg_for_item(frm, pending_rows, idx) {
	if (idx >= pending_rows.length) {
		frm.save(null, function () {
			frappe.show_alert({ message: "All FG items created and linked.", indicator: "green" });
			frm.refresh();
		});
		return;
	}

	var row = pending_rows[idx];
	var suggested_name = (row.item_name || "FG").substring(0, 60);
	var suggested_desc = [row.size, row.material, row.finishing].filter(Boolean).join(" | ");

	var d = new frappe.ui.Dialog({
		title: "Create FG — " + (idx + 1) + " of " + pending_rows.length + ": " + row.item_name,
		fields: [
			{
				fieldtype: "HTML",
				options: "<div style='padding:6px 10px;background:#f0f4ff;border-radius:4px;font-size:12px;color:#1a3a5c;margin-bottom:6px'>"
					+ "Creating Finished Good item for: <b>" + row.item_name + "</b><br>"
					+ "You can also link an existing item using the field below.</div>",
			},
			{
				fieldtype: "Link", fieldname: "existing_item",
				label: "Link Existing Item (optional)", options: "Item",
				description: "Leave blank to create a new item",
			},
			{ fieldtype: "Section Break", label: "New Item Details" },
			{
				fieldtype: "Data", fieldname: "item_name_field",
				label: "Item Name", reqd: 1,
				default: suggested_name,
				description: "Item code is auto-generated by the system",
			},
			{
				fieldtype: "Small Text", fieldname: "description",
				label: "Description",
				default: suggested_desc,
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Link", fieldname: "item_group",
				label: "Item Group", options: "Item Group",
				reqd: 1, default: "Finished Goods",
			},
			{
				fieldtype: "Link", fieldname: "department",
				label: "Department", options: "Department",
				reqd: 1,
				description: "Required — abbreviation is auto-resolved",
			},
			{
				fieldtype: "Link", fieldname: "stock_uom",
				label: "UOM", options: "UOM",
				reqd: 1, default: "Nos",
			},
		],
		primary_action_label: "Create & Next",
		secondary_action_label: "Skip",
		secondary_action: function () {
			d.hide();
			_create_fg_for_item(frm, pending_rows, idx + 1);
		},
		primary_action: function (vals) {
			d.hide();

			if (vals.existing_item) {
				// Link existing item
				_link_fg_to_rows(frm, row.cost_item || row.item_name, vals.existing_item);
				_create_fg_for_item(frm, pending_rows, idx + 1);
				return;
			}

			// Create new Item via backend API — resolves abbreviation fields automatically
			frappe.call({
				method: "nxtgen_savinda_pricing_calculator.api.manufacturing.create_fg_item",
				args: {
					item_name:   vals.item_name_field,
					description: vals.description || vals.item_name_field,
					item_group:  vals.item_group || "Finished Goods",
					department:  vals.department,
					stock_uom:   vals.stock_uom || "Nos",
				},
				freeze: true,
				freeze_message: "Creating FG item...",
				callback: function (r) {
					if (!r.message) return;
					frappe.show_alert({ message: "Created: " + r.message.item_code, indicator: "green" });
					_link_fg_to_rows(frm, row.cost_item || row.item_name, r.message.item_code);
					_create_fg_for_item(frm, pending_rows, idx + 1);
				},
			});
		},
	});
	d.show();
}

function _link_fg_to_rows(frm, cost_item_key, fg_item_code) {
	(frm.doc.items || []).forEach(function (row) {
		var key = row.cost_item || row.item_name;
		if (key === cost_item_key && !row.finish_good) {
			frappe.model.set_value(row.doctype, row.name, "finish_good", fg_item_code);
		}
	});
}


// ─────────────────────────────────────────────────────────────
//  CREATE SALES ORDER FROM WON QUOTATION
//  One SO for multiple FG items — user sets qty per FG
// ─────────────────────────────────────────────────────────────

function _show_create_so_dialog(frm) {
	// Collect unique FG items (one per cost_item group)
	var fg_map = {}, fg_list = [];
	(frm.doc.items || []).forEach(function (row) {
		var key = row.finish_good;
		if (!key) return;
		if (!fg_map[key]) {
			fg_map[key] = {
				item_code:              row.finish_good,
				item_name:              row.item_name,
				qty:                    row.qty,
				rate:                   flt_v(row.selling_price),
				calculation_breakdown:  row.calculation_breakdown,
				cost_item:              row.cost_item,
			};
			fg_list.push(fg_map[key]);
		}
	});

	if (!fg_list.length) {
		frappe.msgprint({ title: "No FG Items", message: "No FG items are linked yet. Use Manufacturing → Create / Link FG Items first.", indicator: "orange" });
		return;
	}

	var fields = [
		{
			fieldtype: "Link", fieldname: "customer",
			label: "Customer", options: "Customer",
			reqd: 1, default: frm.doc.customer,
		},
		{
			fieldtype: "Date", fieldname: "delivery_date",
			label: "Required Delivery Date", reqd: 1,
		},
		{
			fieldtype: "Section Break",
			label: "Select FG Items to include in Sales Order",
		},
	];

	fg_list.forEach(function (item, i) {
		fields.push({
			fieldtype: "Check", fieldname: "sel_" + i,
			label: item.item_code + " — " + item.item_name,
			default: 1,
		});
		fields.push({
			fieldtype: "Float", fieldname: "qty_" + i,
			label: "Quantity", default: item.qty,
			depends_on: "eval:doc.sel_" + i,
		});
		fields.push({
			fieldtype: "Currency", fieldname: "rate_" + i,
			label: "Rate (LKR/unit)", default: item.rate,
			depends_on: "eval:doc.sel_" + i,
		});
		if (i < fg_list.length - 1) {
			fields.push({ fieldtype: "Column Break" });
		}
	});

	var d = new frappe.ui.Dialog({
		title: "Create Sales Order — " + frm.doc.name,
		fields: fields,
		primary_action_label: "Create Sales Order",
		primary_action: function (vals) {
			d.hide();

			var so_items = fg_list
				.filter(function (item, i) { return vals["sel_" + i]; })
				.map(function (item, i) {
					// Find original index for qty/rate fields
					var orig_i = fg_list.indexOf(item);
					return {
						doctype:   "Sales Order Item",
						item_code: item.item_code,
						item_name: item.item_name,
						qty:       flt_v(vals["qty_" + orig_i]) || item.qty,
						rate:      flt_v(vals["rate_" + orig_i]) || item.rate,
					delivery_date: vals.delivery_date,
				};
			});

			frappe.call({
				method: "frappe.client.insert",
				args: {
					doc: {
						doctype:           "Sales Order",
						customer:          vals.customer,
						transaction_date:  frappe.datetime.get_today(),
						delivery_date:     vals.delivery_date,
						items:             so_items,
						status:            "Draft",
					},
				},
				freeze: true,
				freeze_message: "Creating Sales Order...",
				callback: function (r) {
					if (!r.message) return;
					frappe.show_alert({ message: "Sales Order created: " + r.message.name, indicator: "green" });
					frappe.set_route("Form", "Sales Order", r.message.name);
				},
			});
		},
	});
	d.show();
}