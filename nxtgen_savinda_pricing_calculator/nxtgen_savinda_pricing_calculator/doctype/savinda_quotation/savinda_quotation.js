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
		frm.set_query("sales_person", function () {
			return {
				filters: {
					department: "Marketing - SGSPL",
					status: "Active"
				}
			};
		});

		// View the linked Cost Sheet (full costing detail) in a new tab — for checking the
		// costing behind this quote. The Cost Sheet is read-only once submitted.
		if (!frm.is_new() && frm.doc.cost_sheet) {
			frm.add_custom_button(__("View Cost Sheet"), function () {
				window.open("/app/cost-sheet/" + encodeURIComponent(frm.doc.cost_sheet), "_blank");
			}, __("Actions"));
		}
		// Fill from Cost Sheet button (draft only — locked after submit)
		// if (frm.doc.cost_sheet && frm.doc.docstatus === 0) {
		// 	frm.add_custom_button(__("Reload from Cost Sheet"), function () {
		// 		frappe.confirm(
		// 			"This will replace all current items with items from the linked Cost Sheet. Continue?",
		// 			function () {
		// 				frm.clear_table("items");
		// 				_do_load_from_cost_sheet(frm, frm.doc.cost_sheet, function (loaded) {
		// 					frm.refresh_field("items");
		// 					if (loaded) frappe.show_alert({ message: loaded + " item(s) loaded.", indicator: "green" });
		// 				});
		// 			}
		// 		);
		// 	}, __("Fill"));
		// } else if (frm.is_new()) {
		// 	frm.add_custom_button(__("From Cost Sheet"), function () {
		// 		_show_load_dialog(frm);
		// 	}, __("Fill"));
		// }

		// Qty Break buttons — draft only (hidden after submit)
		// if (frm.doc.docstatus === 0) {
		// 	frm.add_custom_button(__("Calculate Qty Breaks"), function () {
		// 		calculate_all_qty_breaks(frm);
		// 	});
		// 	frm.add_custom_button(__("Add Qty Break"), function () {
		// 		show_add_qty_break_dialog(frm);
		// 	});
		// }

		// Print Quotation button
		// if (!frm.is_new()) {
		// 	frm.add_custom_button(__("Print Quotation"), function () {
		// 		frappe.set_route("print", "Savinda Quotation", frm.doc.name, "Savinda Quotation");
		// 	}, __("Actions"));
		// }

		// ── Status colour indicator ───────────────────────────
		var status_colour = {
			"Draft": "grey",
			"Sent": "blue",
			"Accepted": "green",
			"Won": "darkgreen",
			"Lost": "red",
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
							args: { doctype: "Savinda Quotation", name: frm.doc.name, fieldname: "status", value: "Won" },
							callback: function () {
								frm.reload_doc();
								frappe.show_alert({ message: "Quotation marked as Won ✓", indicator: "green" });
							},
						});
					}
				);
			}, __("Actions"));

			frm.add_custom_button(__("Mark as Lost"), function () {
				var d = new frappe.ui.Dialog({
					title: __("Mark Quotation as Lost"),
					fields: [
						{
							fieldtype: "Table MultiSelect", fieldname: "lost_reasons",
							label: __("Lost Reasons"), options: "Opportunity Lost Reason Detail",
						},
						{ fieldtype: "Small Text", fieldname: "detailed_reason", label: __("Detailed Reason") },
					],
					primary_action_label: __("Declare Lost"),
					primary_action: function (values) {
						frm.set_value("status", "Lost");
						frm.set_value("order_lost_reason", values.detailed_reason || "");
						frm.clear_table("lost_reasons");
						(values.lost_reasons || []).forEach(function (r) {
							var reason = r.lost_reason || r;
							if (reason) {
								var row = frm.add_child("lost_reasons");
								row.lost_reason = reason;
							}
						});
						frm.refresh_field("lost_reasons");
						d.hide();
						frm.save(frm.doc.docstatus === 1 ? "Update" : undefined).then(function () {
							frappe.show_alert({ message: "Quotation marked as Lost.", indicator: "red" });
						});
					},
				});
				d.show();
			}, __("Actions"));
		}

		// ── Manufacturing functions (enabled when Won) ─────────────────────
		// Manufacturing prep for NPD — FG items + BOM building are available on any saved
		// quotation (draft or submitted), like the Sales Order's BOM Builder, so BOMs can be
		// built for an NPD sample before the quotation is Won.
		if (!frm.is_new() && frm.doc.docstatus !== 2) {
			frm.add_custom_button(__("Create / Link FG Items"), function () {
				_show_create_fg_dialog(frm);
			}, __("Manufacturing"));

			frm.add_custom_button(__("BOM Builder"), function () {
				window.location.href = "/app/bom-builder?quotation=" + encodeURIComponent(frm.doc.name);
			}, __("Manufacturing"));
		}

		if (!frm.is_new() && frm.doc.status === "Won") {
			// Lead/Prospect-based quotation has no Customer yet → offer to create one
			if (!frm.doc.customer) {
				frm.add_custom_button(__("Create Customer"), function () {
					_create_customer(frm);
				}, __("Manufacturing"));
			}

			frm.add_custom_button(__("Create Sales Order"), function () {
				_show_create_so_dialog(frm, "Sales Order");
			}, __("Manufacturing"));

			// NPD SAMPLE — a New-Product-Development sample (no Sales Order). Creates an NPD
			// Request; on approval it becomes a Manufacture Material Request → Production Plan.
			frm.add_custom_button(__("Create NPD Sample"), function () {
				_create_npd_sample(frm, { quotation: frm.doc.name });
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
				if (!frm.doc.customer_name && opp.customer_name)
					frm.set_value("customer_name", opp.customer_name);
			},
		});
	},

	// When a real Customer is picked, mirror its name and set the quotation currency from the
	// customer (like a standard Quotation) so the Sales Order created from it uses the right
	// currency. Setting currency triggers the exchange-rate fetch + item re-pricing.
	customer: function (frm) {
		if (!frm.doc.customer) return;
		frappe.db.get_value("Customer", frm.doc.customer, "customer_name", function (r) {
			if (r && r.customer_name) frm.set_value("customer_name", r.customer_name);
		});
		if (frm.doc.docstatus !== 0) return;
		frappe.call({
			method: "nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.savinda_quotation.savinda_quotation.get_customer_currency",
			args: { customer: frm.doc.customer },
			callback: function (r) {
				if (r.message && r.message !== frm.doc.currency) {
					frm.set_value("currency", r.message);
				}
			},
		});
	},

	// ── Quotation currency → auto-fetch exchange rate (LKR per 1 unit) ──
	currency: function (frm) {
		_fetch_exchange_rate(frm);
	},
	// Manual exchange-rate edit → re-derive every line's shown rate from its base.
	conversion_rate: function (frm) {
		_reprice_items(frm);
	},
	date: function (frm) {
		if (frm.doc.currency && frm.doc.currency !== _base_currency()) _fetch_exchange_rate(frm);
	},

	// Load the selected Terms & Conditions template into the editable Terms box (like SI)
	terms_template: function (frm) {
		if (!frm.doc.terms_template) return;
		frappe.db.get_value("Terms and Conditions", frm.doc.terms_template, "terms", function (r) {
			if (r && r.terms) frm.set_value("terms", r.terms);
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
				if (!frm.doc.customer_name && cs.customer_name)
					frm.set_value("customer_name", cs.customer_name);
			},
		});
	},
});


// ── Child table events ────────────────────────────────────────
frappe.ui.form.on("Savinda Quotation Item", {
	selling_price: function (frm, cdt, cdn) {
		var row = frappe.get_doc(cdt, cdn);
		var crate = flt_v(frm.doc.conversion_rate) || 1;
		frappe.model.set_value(cdt, cdn, "is_manually_set", 1);
		// User typed a rate in the quotation currency → back-fill the company-base anchor.
		frappe.model.set_value(cdt, cdn, "base_selling_price", flt_v(row.selling_price) * crate);
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

function _base_currency() {
	return (frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency) || "LKR";
}

// Auto-fetch the exchange rate (company base per 1 unit of quotation currency) from
// ERPNext Currency Exchange for the quotation date. The user can still override it.
function _fetch_exchange_rate(frm) {
	var base = _base_currency();
	if (!frm.doc.currency || frm.doc.currency === base) {
		frm.set_value("conversion_rate", 1);
		_reprice_items(frm);
		return;
	}
	frappe.call({
		method: "erpnext.setup.utils.get_exchange_rate",
		args: {
			from_currency: frm.doc.currency,
			to_currency: base,
			transaction_date: frm.doc.date || frappe.datetime.get_today(),
		},
		callback: function (r) {
			if (r && r.message) {
				frm.set_value("conversion_rate", flt_v(r.message));
			}
			_reprice_items(frm);
		},
	});
}

// Set a row's price fields from a company-base (LKR) rate produced by costing.
function _init_row_price(row, base_lkr, conversion_rate, currency) {
	var crate = flt_v(conversion_rate) || 1;
	row.base_selling_price = flt_v(base_lkr);
	row.selling_price = flt_v(base_lkr) / crate;
	row.currency = currency;
	row.is_manually_set = 0;
}

// Re-derive every line's shown (transaction-currency) rate from its fixed company-base
// rate — used when the quotation currency or conversion rate changes. The base is kept.
function _reprice_items(frm) {
	var crate = flt_v(frm.doc.conversion_rate) || 1;
	(frm.doc.items || []).forEach(function (row) {
		row.currency = frm.doc.currency;
		var base = flt_v(row.base_selling_price) || flt_v(row.selling_price);
		row.base_selling_price = base;
		row.selling_price = base / crate;
	});
	frm.refresh_field("items");
}

// Carry the Cost Item's (final, saved) packing info onto a quotation row.
// The inquiry is never re-read here — the Cost Item is the source of truth.
function _apply_packing_q(qrow, ci) {
	if (!qrow || !ci) return;
	qrow.packing_type = ci.packing_type || "";
	qrow.winding_direction = ci.winding_direction || "";
	qrow.pcs_per_role = ci.pcs_per_role || 0;
	qrow.up = ci.up || 0;
	qrow.is_printed = ci.is_printed || "";
}

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
			if (!frm.doc.customer_name && cs.customer_name)
				frm.set_value("customer_name", cs.customer_name);
			if (!frm.doc.sales_person && cs.sales_person)
				frm.set_value("sales_person", cs.sales_person);

			console.log(cs);
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
				qrow.item_name = ci.cost_item_name || cs_row.item_name || cs_row.item;
				qrow.cost_item = cs_row.item;
				qrow.calculation_breakdown = (ci.calculations && ci.calculations[0])
					? (ci.calculations[0].calculation_breakdown || "") : "";
				qrow.size = _format_size(ci);
				qrow.material = ci.material || "";
				// Finishing ('XX Colors + finishings') + BOM remark are filled server-side in
				// validate() from the cost item — leave blank here so they populate on save.
				qrow.finishing = "";
				qrow.finishing_variant = variant || 1;
				qrow.qty = qty_override !== undefined ? qty_override : flt_v(cs_row.qty);
				qrow.unit_cost = flt_v(cs_row.unit_price);
				var base_lkr = flt_v(cs_row.selling_unit_price) > 0
					? flt_v(cs_row.selling_unit_price)
					: flt_v(cs_row.unit_price);
				_init_row_price(qrow, base_lkr, frm.doc.conversion_rate, frm.doc.currency);
				// Header profit_margin (lowest across items) is set server-side in validate().
				_apply_packing_q(qrow, ci);
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
							item_name: ci.cost_item_name || cs_row.item_name || cs_row.item,
							cost_item: cs_row.item,
							cb_name: cb_name,
							size: _format_size(ci),
							material: ci.material || "",
							finishing: "",  // filled server-side in validate() from the cost item
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
									qrow.item_name = base_info.item_name;
									qrow.cost_item = base_info.cost_item;
									qrow.calculation_breakdown = base_info.cb_name;
									qrow.size = base_info.size;
									qrow.material = base_info.material;
									qrow.finishing = base_info.finishing;
									qrow.finishing_variant = 1;
									qrow.qty = qb.qty;
									qrow.is_manually_set = 0;
									_apply_packing_q(qrow, ci);

									if (r3.message && !r3.message.error) {
										qrow.unit_cost = flt_v(r3.message.unit_cost);
										_init_row_price(qrow, flt_v(r3.message.sell_unit), frm.doc.conversion_rate, frm.doc.currency);
									} else {
										// Fallback if CB has no ui_state yet
										qrow.unit_cost = flt_v(cs_row.unit_price);
										_init_row_price(qrow, flt_v(cs_row.selling_unit_price) || flt_v(cs_row.unit_price), frm.doc.conversion_rate, frm.doc.currency);
									}

									loaded++;
									add_break_row(qb_idx + 1);
								},
								error: function () {
									// Network / server error — fall back to base price
									var qrow = frm.add_child("items");
									qrow.item_name = base_info.item_name;
									qrow.cost_item = base_info.cost_item;
									qrow.calculation_breakdown = base_info.cb_name;
									qrow.size = base_info.size;
									qrow.material = base_info.material;
									qrow.finishing = base_info.finishing;
									qrow.finishing_variant = 1;
									qrow.qty = qb.qty;
									qrow.unit_cost = flt_v(cs_row.unit_price);
									_init_row_price(qrow, flt_v(cs_row.selling_unit_price) || flt_v(cs_row.unit_price), frm.doc.conversion_rate, frm.doc.currency);
									_apply_packing_q(qrow, ci);
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
						qrow.item_name = cs_row.item_name || cs_row.item;
						qrow.cost_item = cs_row.item;
						qrow.qty = flt_v(cs_row.qty);
						qrow.unit_cost = flt_v(cs_row.unit_price);
						_init_row_price(qrow, flt_v(cs_row.selling_unit_price) || flt_v(cs_row.unit_price), frm.doc.conversion_rate, frm.doc.currency);
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
					_init_row_price(qrow, flt_v(res.sell_unit), frm.doc.conversion_rate, frm.doc.currency);

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
					// Direct assignment (not set_value) so the manual-edit handler doesn't fire.
					row.unit_cost = flt_v(r.message.unit_cost);
					_init_row_price(row, flt_v(r.message.sell_unit), frm.doc.conversion_rate, frm.doc.currency);
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

	// Item Group is fetched from the linked Inquiry (falls back to Finished Goods)
	if (frm.doc.inquiry) {
		frappe.db.get_value("Opportunity", frm.doc.inquiry, "custom_item_group", function (r) {
			_create_fg_for_item(frm, pending, 0, (r && r.custom_item_group) || "Finished Goods", []);
		});
	} else {
		_create_fg_for_item(frm, pending, 0, "Finished Goods", []);
	}
}

function _create_fg_for_item(frm, pending_rows, idx, ig_default, created) {
	ig_default = ig_default || "Finished Goods";
	created = created || [];
	if (idx >= pending_rows.length) {
		frm.save(null, function () {
			frappe.show_alert({ message: "All FG items created and linked.", indicator: "green" });
			frm.refresh();
			// After the FGs are created, open the qty-pricing popup to review/edit the tiers
			// and write the Pricing Rules used when the Sales Order is created.
			if (created.length && window.nxtgen_pricing) {
				nxtgen_pricing.showForFGs(frm, created, function () {});
			}
		});
		return;
	}

	var row = pending_rows[idx];
	// Fetch the Product Library review defaults for this cost item, then open the dialog.
	frappe.call({
		method: "nxtgen_savinda_pricing_calculator.api.production_plan.get_cost_item_fg_defaults",
		args: { cost_item: row.cost_item || "", quotation: frm.doc.name },
		callback: function (r) {
			_build_fg_dialog(frm, pending_rows, idx, ig_default, row, r.message || {}, created);
		},
	});
}

function _build_fg_dialog(frm, pending_rows, idx, ig_default, row, pd, created) {
	created = created || [];
	var suggested_name = (row.item_name || "FG").substring(0, 60);
	var suggested_desc = [row.size, row.material, row.finishing].filter(Boolean).join(" | ");
	var pl_fields = pd.pl_fields || [];
	var pl_defaults = pd.defaults || {};
	var fields = [
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
			reqd: 1, default: ig_default,
			description: "Fetched from the linked Inquiry — change if needed.",
		},
		{
			fieldtype: "Link", fieldname: "department",
			label: "Department", options: "Department",
			reqd: 1, default: pl_defaults.department,
			description: "Required — abbreviation is auto-resolved",
		},
		{
			fieldtype: "Link", fieldname: "stock_uom",
			label: "UOM", options: "UOM",
			reqd: 1, default: "Nos",
		},
	];

	// Product Library details — reviewed here, saved into the Product Library record.
	if (pl_fields.length) {
		fields.push({ fieldtype: "Section Break", label: "Product Library Details", collapsible: 1 });
		nxtgen_pl.fields(pl_fields, pd.is_flexo, "", pl_defaults).forEach(function (f) { fields.push(f); });
	}

	fields.push({ fieldtype: "Section Break", label: "Variants (optional)" });
	fields.push({
		fieldtype: "Check", fieldname: "has_variant",
		label: "Has Variant (create S / M / L … as variant items)",
		description: "Tick to make this a template and create ERPNext variants. Each variant is its own FG item; all share this Cost Item.",
	});
	fields.push({
		fieldtype: "Data", fieldname: "variant_attribute",
		label: "Variant Attribute", default: "Size",
		depends_on: "has_variant",
		description: "e.g. Size. The values below are added to this Item Attribute.",
	});
	fields.push({
		fieldtype: "Table", fieldname: "variants",
		label: "Variations", depends_on: "has_variant",
		cannot_add_rows: false, in_place_edit: false, data: [],
		fields: [
			{ fieldtype: "Data", fieldname: "value", label: "Variant (e.g. S)", in_list_view: 1, reqd: 1, columns: 2 },
			{ fieldtype: "Data", fieldname: "item_name", label: "Item Name (blank = auto)", in_list_view: 1, columns: 5 },
			{ fieldtype: "Data", fieldname: "customer_ref", label: "Customer Ref", in_list_view: 1, columns: 3 },
		],
	});

	var d = new frappe.ui.Dialog({
		title: "Create FG — " + (idx + 1) + " of " + pending_rows.length + ": " + row.item_name,
		size: "extra-large",
		fields: fields,
		primary_action_label: "Create & Next",
		secondary_action_label: "Skip",
		secondary_action: function () {
			d.hide();
			_create_fg_for_item(frm, pending_rows, idx + 1, ig_default, created);
		},
		primary_action: function (vals) {
			d.hide();

			if (vals.existing_item) {
				// Link existing item
				_link_fg_to_rows(frm, row.cost_item || row.item_name, vals.existing_item);
				created.push({ item_code: vals.existing_item, item_name: row.item_name, cost_item: row.cost_item || "" });
				_create_fg_for_item(frm, pending_rows, idx + 1, ig_default, created);
				return;
			}

			// Reviewed Product Library values (shared across variants).
			var pl_overrides = nxtgen_pl.overrides(pl_fields, vals, "");

			// ── Variant path: create a template + native ERPNext variants ──
			if (vals.has_variant) {
				var variants = (d.get_value("variants") || []).filter(function (v) {
					return (v.value || "").trim();
				});
				if (!variants.length) {
					frappe.msgprint({ message: "Add at least one variant row, or untick 'Has Variant'.", indicator: "orange" });
					d.show();
					return;
				}
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.manufacturing.create_fg_variants",
					args: {
						template_name: vals.item_name_field,
						description: vals.description || vals.item_name_field,
						item_group: vals.item_group || "Finished Goods",
						department: vals.department,
						stock_uom: vals.stock_uom || "Nos",
						attribute: vals.variant_attribute || "Size",
						variants: JSON.stringify(variants),
						cost_item: row.cost_item || "",
						pl_overrides: JSON.stringify(pl_overrides),
					},
					freeze: true,
					freeze_message: "Creating template + variant items...",
					callback: function (r) {
						if (!r.message) return;
						var items = r.message.items || [];
						frappe.show_alert({
							message: "Created " + items.length + " FG item(s): "
								+ items.map(function (it) { return it.item_code; }).join(", "),
							indicator: "green",
						});
						// Link the first created FG to the quotation row(s) for this cost item
						if (items.length) {
							_link_fg_to_rows(frm, row.cost_item || row.item_name, items[0].item_code);
						}
						// Each variant is its own sellable FG → price every variant.
						items.forEach(function (it) {
							created.push({ item_code: it.item_code, item_name: it.item_name || it.item_code, cost_item: row.cost_item || "" });
						});
						_create_fg_for_item(frm, pending_rows, idx + 1, ig_default, created);
					},
				});
				return;
			}

			// Create new Item via backend API — resolves abbreviation fields automatically
			frappe.call({
				method: "nxtgen_savinda_pricing_calculator.api.manufacturing.create_fg_item",
				args: {
					item_name: vals.item_name_field,
					description: vals.description || vals.item_name_field,
					item_group: vals.item_group || "Finished Goods",
					department: vals.department,
					stock_uom: vals.stock_uom || "Nos",
					cost_item: row.cost_item || "",
					pl_overrides: JSON.stringify(pl_overrides),
				},
				freeze: true,
				freeze_message: "Creating FG item...",
				callback: function (r) {
					if (!r.message) return;
					frappe.show_alert({ message: "Created: " + r.message.item_code, indicator: "green" });
					_link_fg_to_rows(frm, row.cost_item || row.item_name, r.message.item_code);
					created.push({ item_code: r.message.item_code, item_name: vals.item_name_field, cost_item: row.cost_item || "" });
					_create_fg_for_item(frm, pending_rows, idx + 1, ig_default, created);
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

// Create a Customer from a lead/prospect-based quotation
function _create_customer(frm) {
	frappe.confirm(
		"Create a Customer <b>" + (frm.doc.customer_name || "") + "</b> from this quotation and link it?",
		function () {
			frappe.call({
				method: "nxtgen_savinda_pricing_calculator.api.manufacturing.create_customer_from_quotation",
				args: { quotation: frm.doc.name },
				freeze: true, freeze_message: "Creating customer...",
				callback: function (r) {
					if (r.message && r.message.customer) {
						frappe.show_alert({ message: "Customer: " + r.message.customer, indicator: "green" });
						frm.reload_doc();
					}
				},
			});
		}
	);
}

// Create an NPD sample request (no Sales Order) from a Quotation or Cost Sheet. Prompts for the
// sample qty + required date, creates the NPD Request, and routes to it for approval.
function _create_npd_sample(frm, opts) {
	frappe.prompt(
		[
			{ fieldtype: "Int", fieldname: "sample_qty", label: __("Sample Qty"), reqd: 1, default: 1 },
			{ fieldtype: "Date", fieldname: "required_date", label: __("Required Date"),
			  default: frappe.datetime.add_days(frappe.datetime.get_today(), 7) },
		],
		function (v) {
			var method = opts.quotation
				? "nxtgen_savinda_pricing_calculator.api.production_plan.create_npd_request_from_quotation"
				: "nxtgen_savinda_pricing_calculator.api.production_plan.create_npd_request_from_cost_sheet";
			var args = opts.quotation ? { quotation: opts.quotation } : { cost_sheet: opts.cost_sheet };
			frappe.call({
				method: method, args: args,
				freeze: true, freeze_message: __("Creating NPD Sample Request…"),
				callback: function (r) {
					var npd = (r.message || {}).npd_request;
					if (!npd) { return; }
					frappe.db.set_value("NPD Request", npd, {
						sample_qty: v.sample_qty || 1,
						required_date: v.required_date || null,
					}).then(function () {
						frappe.set_route("Form", "NPD Request", npd);
					});
				},
			});
		},
		__("Create NPD Sample"), __("Create")
	);
}

function _show_create_so_dialog(frm, order_type) {
	order_type = order_type || "Sales Order";
	// Fetch ALL FG items tied to this quotation's cost items (variants included)
	frappe.call({
		method: "nxtgen_savinda_pricing_calculator.api.manufacturing.get_quotation_fg_items",
		args: { quotation: frm.doc.name },
		freeze: true, freeze_message: "Loading FG items...",
		callback: function (r) {
			var fg_list = r.message || [];
			if (!fg_list.length) {
				frappe.msgprint({ title: "No FG Items", message: "No FG items are linked yet. Use Manufacturing → Create / Link FG Items first.", indicator: "orange" });
				return;
			}
			_render_so_dialog(frm, fg_list, order_type);
		},
	});
}

function _render_so_dialog(frm, fg_list, order_type) {
	order_type = order_type || "Sales Order";
	var is_npd = order_type === "NPD";
	var action_label = is_npd ? "Create NPD" : "Create Sales Order";
	// Pre-fill the grid (all rows ticked by default)
	var grid_data = fg_list.map(function (it) {
		return {
			include: 1,
			item_code: it.item_code,
			item_name: it.item_name,
			qty: flt_v(it.qty),
			rate: flt_v(it.rate),
		};
	});
	// Packing snapshot per FG (carried onto the Sales Order line; editable there per PO)
	var pk_map = {};
	fg_list.forEach(function (it) { pk_map[it.item_code] = it; });

	var d = new frappe.ui.Dialog({
		title: action_label + " — " + frm.doc.name,
		size: "large",
		fields: [
			{
				fieldtype: "Link", fieldname: "customer",
				label: "Customer", options: "Customer",
				reqd: 1, default: frm.doc.customer,
				description: frm.doc.customer ? "" : "No customer linked — use Manufacturing → Create Customer first, or pick one here.",
			},
			{
				fieldtype: "Date", fieldname: "delivery_date",
				label: "Required Delivery Date", reqd: 1,
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Data", fieldname: "po_no",
				label: "Customer's PO No", reqd: 1,
				description: "The customer's purchase order reference (required on the Sales Order).",
			},
			{
				fieldtype: "Date", fieldname: "po_date",
				label: "Customer's PO Date",
			},
			{ fieldtype: "Section Break", label: "Finished Goods — tick the ones to book" + (is_npd ? " in the NPD order" : " in the Sales Order") },
			{
				fieldtype: "Table", fieldname: "fg_items",
				cannot_add_rows: true, in_place_edit: false, data: grid_data,
				fields: [
					{ fieldtype: "Check", fieldname: "include", label: "Book", in_list_view: 1, columns: 1, default: 1 },
					{ fieldtype: "Data", fieldname: "item_code", label: "FG Item", in_list_view: 1, read_only: 1, columns: 3 },
					{ fieldtype: "Data", fieldname: "item_name", label: "Name", in_list_view: 1, read_only: 1, columns: 4 },
					{ fieldtype: "Float", fieldname: "qty", label: "Qty", in_list_view: 1, columns: 2 },
					{ fieldtype: "Currency", fieldname: "rate", label: "Rate (LKR)", in_list_view: 1, columns: 2 },
				],
			},
		],
		primary_action_label: action_label,
		primary_action: function (vals) {
			var rows = (d.get_value("fg_items") || []).filter(function (x) { return x.include && x.item_code; });
			if (!rows.length) {
				frappe.msgprint({ message: "Tick at least one FG item to book.", indicator: "orange" });
				return;
			}
			d.hide();
			// Quotation currency + rate (LKR per 1 unit). Costing rates are in base LKR,
			// so the SO line rate = base rate / conversion_rate (in the quotation currency).
			var cur = frm.doc.currency || _base_currency();
			var crate = flt_v(frm.doc.conversion_rate) || 1;
			var so_items = rows.map(function (x) {
				var pk = pk_map[x.item_code] || {};
				return {
					doctype: "Sales Order Item",
					item_code: x.item_code,
					item_name: x.item_name,
					qty: flt_v(x.qty) || 1,
					// No rate here — the qty Pricing Rules price each line automatically (see below).
					delivery_date: vals.delivery_date,
					custom_packing_type: pk.custom_packing_type || "",
					custom_winding_direction: pk.custom_winding_direction || "",
					custom_pcs_per_role: pk.custom_pcs_per_role || 0,
					custom_up: pk.custom_up || 0,
					custom_is_printed: pk.custom_is_printed || "",
				};
			});

			frappe.call({
				method: "frappe.client.insert",
				args: {
					doc: {
						doctype: "Sales Order",
						customer: vals.customer,
						custom_order_type: order_type,
						transaction_date: frappe.datetime.get_today(),
						delivery_date: vals.delivery_date,
						po_no: vals.po_no || "",
						po_date: vals.po_date || null,
						currency: cur,
						conversion_rate: crate,
						// Let ERPNext apply the qty Pricing Rules (auto-created on FG creation)
						// so each line is priced by quantity — not copied from the quotation.
						ignore_pricing_rule: 0,
						items: so_items,
						status: "Draft",
					},
				},
				freeze: true,
				freeze_message: action_label + "...",
				callback: function (r) {
					if (!r.message) return;
					frappe.show_alert({
						message: (is_npd ? "NPD order created: " : "Sales Order created: ") + r.message.name
							+ (is_npd ? " — send for NPD approval" : ""),
						indicator: "green",
					});
					frappe.set_route("Form", "Sales Order", r.message.name);
				},
			});
		},
	});
	d.show();
}

// ── Amend flow + cancel cascade (merges with the main form handlers above) ──
frappe.ui.form.on("Savinda Quotation", {
	// Warn before cancelling: cancelling the quotation cascades to the Cost Sheet + its CBs.
	before_cancel: function (frm) {
		if (!frm.doc.cost_sheet) { return; }
		return new Promise(function (resolve, reject) {
			frappe.confirm(
				__("Cancelling this quotation will also cancel its Cost Sheet <b>{0}</b> and the linked cost breakdowns (unless another submitted quotation still uses that cost sheet).<br><br>Continue?", [frm.doc.cost_sheet]),
				function () { resolve(); },   // Yes -> proceed with cancel
				function () { reject(); }      // No  -> abort cancel
			);
		});
	},

	refresh: function (frm) {
		// A freshly AMENDED draft quotation points at the CANCELLED Cost Sheet + CBs, so it cannot
		// be saved as-is ("Cannot link cancelled document"). Re-point it to an editable draft copy.
		if (frm.doc.docstatus === 0 && frm.doc.amended_from && frm.doc.cost_sheet) {
			frm.add_custom_button(__("Amend Cost Sheet & Reload"), function () {
				_amend_cost_sheet_and_reload(frm, true);
			}, __("Actions")).addClass("btn-primary");

			// Auto-run ONCE when the linked cost sheet is still cancelled, so the draft is saveable.
			if (!frm.__amend_relink_checked) {
				frm.__amend_relink_checked = true;
				frappe.db.get_value("Cost Sheet", frm.doc.cost_sheet, "docstatus").then(function (r) {
					if (r && r.message && parseInt(r.message.docstatus, 10) === 2) {
						_amend_cost_sheet_and_reload(frm, false);
					}
				});
			}
		}
	},
});

// Amend the linked (cancelled) Cost Sheet into an editable draft copy, re-point this quotation
// to it, and reload the item prices from the new (draft) cost breakdowns. Works on an UNSAVED
// (amended) quotation — it takes the cost sheet name, not the quotation.
function _amend_cost_sheet_and_reload(frm, withConfirm) {
	var run = function () {
		var old_cs = frm.doc.cost_sheet;
		if (!old_cs) { frappe.msgprint(__("This quotation has no linked Cost Sheet.")); return; }
		frappe.call({
			method: "nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.savinda_quotation.savinda_quotation.amend_cost_sheet",
			args: { cost_sheet: old_cs },
			freeze: true, freeze_message: __("Preparing editable cost sheet…"),
			callback: function (r) {
				if (!(r.message && r.message.cost_sheet)) { return; }
				var new_cs = r.message.cost_sheet;
				// Re-point + rebuild items from the new (draft) cost sheet so NO cancelled links remain.
				frm.set_value("cost_sheet", new_cs);
				frm.clear_table("items");
				_do_load_from_cost_sheet(frm, new_cs, function (loaded) {
					frm.refresh_field("items");
					frappe.show_alert({
						message: __("Editable Cost Sheet {0} linked; {1} item(s) reloaded. Edit the CB prices, then Save.", [new_cs, loaded || 0]),
						indicator: "green",
					});
				});
			},
		});
	};
	if (withConfirm) {
		frappe.confirm(__("Create/refresh an editable Cost Sheet with copies of the cost breakdowns and reload the item prices?"), run);
	} else {
		run();
	}
}
