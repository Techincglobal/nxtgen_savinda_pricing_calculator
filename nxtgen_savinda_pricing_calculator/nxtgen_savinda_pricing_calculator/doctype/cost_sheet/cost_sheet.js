// Copyright (c) 2026, Techincglobal.com and contributors
// Cost Sheet — Client Script
//
// HTML panel inside each Cost Sheet Items row:
//  ┌─────────────────────────────────────────────────────┐
//  │  Calculations                          [+ Add]      │
//  │  ┌──────────────────┬──────────┬─────┬──────┐      │
//  │  │ Breakdown        │ Desc     │Unit │ ✏️ 🗑│      │
//  │  ├──────────────────┼──────────┼─────┼──────┤      │
//  │  │ CB-2026-04.00001 │ Cover    │42.00│ ✏️ 🗑│      │
//  │  │ CB-2026-04.00002 │ Inner    │18.00│ ✏️ 🗑│      │
//  │  └──────────────────┴──────────┴─────┴──────┘      │
//  │  ─────────────────────────────────────────────      │
//  │  Pricing Summary                                    │
//  │  Unit Cost (sum)          LKR  60.00               │
//  │  SSCL (2.25%)             LKR   1.35  [✅]         │
//  │  VAT  (18%)               LKR  11.07  [✅]         │
//  │  Selling Unit Price       LKR  72.42               │
//  │  ─────────────────────────────────────────────      │
//  │  Cost Amount              LKR  60,000               │
//  │  Selling Amount           LKR  72,420               │
//  └─────────────────────────────────────────────────────┘

frappe.ui.form.on("Cost Sheet", {

	refresh: function (frm) {
		frm.add_custom_button(__("Create Breakdown"), function () {
			run_create_breakdown(frm);
		}, __("Actions"));
		frm.add_custom_button(__("Refresh Prices"), function () {
			refresh_all_prices(frm);
		}, __("Actions"));
		// Create Quotation from this Cost Sheet
		if (!frm.is_new()) {
			frm.add_custom_button(__("Create Quotation"), function () {
				// Build minimal doc — only pass cost_sheet and inquiry
				// The quotation's before_insert will auto-fill customer from cost sheet
				// The quotation's refresh will auto-load items from cost sheet
				frappe.call({
					method: "frappe.client.insert",
					args: {
						doc: {
							doctype: "Savinda Quotation",
							cost_sheet: frm.doc.name,
							inquiry: frm.doc.inquiry || "",
						},
					},
					callback: function (r) {
						if (r.message) {
							frappe.show_alert({ message: "Quotation created — loading items…", indicator: "green" });
							frappe.set_route("Form", "Savinda Quotation", r.message.name);
						}
					},
				});
			}, __("Actions"));
		}
	},

	inquiry: function (frm) {
		if (!frm.doc.inquiry) return;
		frappe.call({
			method: "frappe.client.get",
			args: { doctype: "Opportunity", name: frm.doc.inquiry },
			callback: function (r) {
				if (!r.message) return;
				var o = r.message;
				if (!frm.doc.subject) frm.set_value("subject", o.custom_subject || "");
				if (!frm.doc.customer_name) frm.set_value("customer_name", o.customer_name || "");
				if (!frm.doc.colour) frm.set_value("colour", o.custom_colour || 0);
				if (!frm.doc.item_group) frm.set_value("item_group", o.custom_item_group || "");
				if (!frm.doc.tiep) frm.set_value("tiep", o.custom_tiep || "");

				// Copy finishing operations from inquiry (only if table is currently empty)
				if (!frm.doc.operations || !frm.doc.operations.length) {
					(o.custom_operations || []).forEach(function (op) {
						if (op.disabled) return;
						var row = frm.add_child("operations");
						row.operation = op.operation;
						row.remarks = op.remarks || "";
					});
					frm.refresh_field("operations");
				}
			},
		});
	},
});

// ── Child table events ────────────────────────────────────────
frappe.ui.form.on("Cost Sheet Items", {

	form_render: function (frm, cdt, cdn) {
		render_panel(frm, cdt, cdn);
	},

	qty: function (frm, cdt, cdn) {
		recalc_row(frm, cdt, cdn);
	},

	sscl: function (frm, cdt, cdn) {
		recalc_row(frm, cdt, cdn);
	},

	vat: function (frm, cdt, cdn) {
		recalc_row(frm, cdt, cdn);
	},
});


// ─────────────────────────────────────────────────────────────
//  RECALCULATE row amounts and re-render panel
// ─────────────────────────────────────────────────────────────

function recalc_row(frm, cdt, cdn) {
	var row = locals[cdt][cdn];

	// unit_price is set by refresh_all_prices (sum of CB unit costs)
	var unit = flt_v(row.unit_price);
	var qty = flt_v(row.qty);
	var sscl = unit * 0.0225;
	var sscl_total = row.sscl ? sscl : 0;
	var vat = (unit + sscl_total) * 0.18;
	var vat_total = row.vat ? vat : 0;
	var sell_unit = round2(unit + sscl_total + vat_total);

	frappe.model.set_value(cdt, cdn, "selling_unit_price", sell_unit);
	frappe.model.set_value(cdt, cdn, "ammount", round2(qty * unit));
	frappe.model.set_value(cdt, cdn, "selling_ammount", round2(qty * sell_unit));

	render_panel(frm, cdt, cdn);
}


// ─────────────────────────────────────────────────────────────
//  RENDER HTML PANEL inside expanded row
// ─────────────────────────────────────────────────────────────

function render_panel(frm, cdt, cdn) {
	var row = locals[cdt][cdn];
	var item_name = row.item; // Cost Item name/id
	if (!item_name) return;

	// Load Cost Item calculations list from DB
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "cost Item", name: item_name },
		callback: function (r) {
			if (!r.message) return;
			var ci = r.message;
			var calcs = ci.calculations || [];

			// Compute unit_price = sum of all CB unit_costs
			var unit_cost_sum = calcs.reduce(function (s, c) { return s + flt_v(c.unit_cost); }, 0);

			// Update unit_price silently if changed
			if (Math.abs(flt_v(row.unit_price) - unit_cost_sum) > 0.001) {
				frappe.model.set_value(cdt, cdn, "unit_price", round2(unit_cost_sum));
				row = locals[cdt][cdn]; // re-read
			}

			var unit = round2(unit_cost_sum);
			var qty = flt_v(row.qty);
			var sscl_amt = row.sscl ? round2(unit * 0.0225) : 0;
			var vat_base = unit + sscl_amt;
			var vat_amt = row.vat ? round2(vat_base * 0.18) : 0;
			var sell_unit = round2(unit + sscl_amt + vat_amt);
			var cost_amt = round2(qty * unit);
			var sell_amt = round2(qty * sell_unit);

			// Build calculation list rows
			var calc_rows = "";
			if (calcs.length === 0) {
				calc_rows = "<tr><td colspan='4' style='padding:8px;color:#aaa;font-style:italic;text-align:center'>"
					+ "No calculations yet. Click + Add to create one.</td></tr>";
			} else {
				calcs.forEach(function (c) {
					calc_rows +=
						"<tr style='border-bottom:1px solid #f0f0f0'>"
						+ "<td style='padding:5px 8px;font-size:11.5px;font-family:monospace'>"
						+ (c.calculation_breakdown || "") + "</td>"
						+ "<td style='padding:5px 8px;font-size:11.5px;color:#555'>"
						+ (c.description || "") + "</td>"
						+ "<td style='padding:5px 8px;text-align:right;font-family:monospace;font-weight:600'>"
						+ "LKR " + cur_fmt(c.unit_cost) + "</td>"
						+ "<td style='padding:5px 8px;text-align:center;white-space:nowrap'>"
						+ "<button class='btn-edit-calc btn btn-xs btn-default' "
						+ "data-cb='" + c.calculation_breakdown + "' style='margin-right:4px'>✏️</button>"
						+ "<button class='btn-remove-calc btn btn-xs btn-danger' "
						+ "data-cb='" + c.calculation_breakdown + "' "
						+ "data-row='" + c.name + "'>🗑</button>"
						+ "</td></tr>";
				});
			}

			// Build tax rows
			var tax_rows = "";
			if (row.sscl) tax_rows += price_row("SSCL (2.25%)", sscl_amt, "#fff8e1");
			if (row.vat) tax_rows += price_row("VAT (18%)", vat_amt, "#fff8e1");

			var html =
				"<div style='padding:12px 14px;background:#f8fafc;border-radius:5px;border:1px solid #e5e7eb'>"

				// ── Calculation list header ──
				+ "<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:8px'>"
				+ "<span style='font-size:12px;font-weight:700;color:#1a3a5c'>Calculations"
				+ (calcs.length ? " <span style='font-size:10px;background:#e0e7ff;color:#3730a3;"
					+ "border-radius:10px;padding:1px 7px;font-weight:600'>" + calcs.length + "</span>" : "")
				+ "</span>"
				+ "<button class='btn-add-calc btn btn-xs btn-primary' "
				+ "data-item='" + item_name + "' data-cdt='" + cdt + "' data-cdn='" + cdn + "'>"
				+ "+ Add Calculation</button>"
				+ "</div>"

				// ── Calculation list table ──
				+ "<div style='border:1px solid #e5e7eb;border-radius:4px;overflow:hidden;margin-bottom:12px'>"
				+ "<table style='width:100%;border-collapse:collapse'>"
				+ "<thead><tr style='background:#1a3a5c'>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:10.5px;text-align:left'>Breakdown</th>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:10.5px;text-align:left'>Description</th>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:10.5px;text-align:right'>Unit Cost</th>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:10.5px;text-align:center'>Actions</th>"
				+ "</tr></thead><tbody>" + calc_rows + "</tbody></table></div>"

				// ── Pricing summary ──
				+ "<div style='background:#fff;border:1px solid #e5e7eb;border-radius:4px;overflow:hidden'>"
				+ "<div style='padding:6px 10px;background:#f0f4ff;font-size:10.5px;font-weight:700;color:#1a3a5c;text-transform:uppercase;letter-spacing:.04em'>Pricing Summary</div>"
				+ "<table style='width:100%;border-collapse:collapse'>"
				+ price_row("Unit Cost (sum of calculations)", unit, "#f0f4ff")
				+ tax_rows
				+ price_row("Selling Unit Price", sell_unit, "#f0fff4")
				+ "<tr><td colspan='2'><hr style='margin:0;border-color:#e5e7eb'></td></tr>"
				+ price_row("Cost Amount  (unit × qty " + qty.toLocaleString() + ")", cost_amt, "#fff")
				+ price_row("Selling Amount", sell_amt, "#dcfce7")
				+ "</table></div></div>";

			// Inject HTML into the html_sfyo field
			setTimeout(function () {
				var grid_row = frm.fields_dict.pricing_list &&
					frm.fields_dict.pricing_list.grid.grid_rows_by_docname[cdn];
				if (!grid_row || !grid_row.grid_form) return;
				var fld = grid_row.grid_form.fields_dict.html_sfyo;
				if (!fld || !fld.wrapper) return;

				var $w = $(fld.wrapper);
				$w.html(html);

				// ── Add Calculation button ──────────────────────
				$w.off("click.add_calc").on("click.add_calc", ".btn-add-calc", function () {
					show_add_calc_popup(frm, cdt, cdn, item_name);
				});

				// ── Edit (open calculator) button ───────────────
				$w.off("click.edit_calc").on("click.edit_calc", ".btn-edit-calc", function () {
					var cb = $(this).data("cb");
					if (cb) {
						var pt = frm.doc.pricing_type || "Offset";
						// No operations param on edit — saved CB already has its own selections
						var url = "/app/offset-calculator?ref=" + encodeURIComponent(cb)
							+ "&cost_sheet=" + encodeURIComponent(frm.doc.name)
							+ "&pricing_type=" + encodeURIComponent(pt);
						window.location.href = url;
					}
				});

				// ── Remove calculation button ───────────────────
				$w.off("click.remove_calc").on("click.remove_calc", ".btn-remove-calc", function () {
					var cb = $(this).data("cb");
					var row_name = $(this).data("row");
					frappe.confirm(
						"Remove calculation <b>" + cb + "</b> from this item?",
						function () {
							// Remove row from Cost Item calculations table
							frappe.call({
								method: "frappe.client.get",
								args: { doctype: "cost Item", name: item_name },
								callback: function (r2) {
									if (!r2.message) return;
									var doc = r2.message;
									doc.calculations = (doc.calculations || []).filter(function (c) {
										return c.calculation_breakdown !== cb;
									});
									frappe.call({
										method: "frappe.client.save",
										args: { doc: doc },
										callback: function () {
											frappe.show_alert({ message: "Removed.", indicator: "green" });
											render_panel(frm, cdt, cdn);
											refresh_row_prices(frm, cdt, cdn);
										},
									});
								},
							});
						}
					);
				});

			}, 300);
		},
	});
}


// ─────────────────────────────────────────────────────────────
//  ADD CALCULATION POPUP
//  Pre-fills item_qty and no_of_colors from Cost Item
//  User fills sheet params → creates Calculation Breakdown
//  → Opens calculator
// ─────────────────────────────────────────────────────────────

function show_add_calc_popup(frm, cdt, cdn, item_name) {

	var pricingType = frm.doc.pricing_type || "Offset";

	// Load Cost Item to get qty and colour
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "cost Item", name: item_name },
		callback: function (r) {
			if (!r.message) return;
			var ci = r.message;

			// Common fields
			var fields = [
				{
					fieldtype: "HTML",
					options: "<div style='padding:7px 10px;background:#f0f4ff;border-radius:4px;"
						+ "font-size:12px;color:#1a3a5c;margin-bottom:6px'>"
						+ "Pricing Type: <b>" + pricingType + "</b>. "
						+ "Fill in the specifications below. You can edit all cost details "
						+ "in the calculator that opens next.</div>",
				},
				{
					fieldtype: "Link", fieldname: "base_material",
					label: "Base Material", options: "Item", reqd: 1,
				},
			];

			if (pricingType === "Flexo") {
				// Flexo: reel dimensions (stored only in ui_state, no dedicated CB fields)
				fields = fields.concat([
					{ fieldtype: "Section Break", label: "Reel Dimensions (mm)" },
					{ fieldtype: "Float", fieldname: "reel_width_mm", label: "Reel Width (mm)", reqd: 1 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Float", fieldname: "product_width_mm", label: "Product Width (mm)", reqd: 1 },
					{ fieldtype: "Section Break", label: "Product Dimensions (mm)" },
					{ fieldtype: "Float", fieldname: "product_length_mm", label: "Product Length (mm)", reqd: 1 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Float", fieldname: "product_margin_mm", label: "Margin (mm)", default: 4 },
					{ fieldtype: "Section Break" },
					{ fieldtype: "Float", fieldname: "product_gap_mm", label: "Gap (mm)", default: 3 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Int", fieldname: "no_of_colors", label: "No of Colors", default: ci.colour || 0 },
				]);
			} else {
				// Offset: sheet dimensions (saved to CB doctype fields)
				fields = fields.concat([
					{ fieldtype: "Section Break", label: "Full Sheet (Inches)" },
					{ fieldtype: "Float", fieldname: "full_sheet_l", label: "Full Sheet Length (L)", reqd: 1 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Float", fieldname: "full_sheet_w", label: "Full Sheet Width (W)", reqd: 1 },
					{ fieldtype: "Section Break", label: "Cut Sheet (Inches)" },
					{ fieldtype: "Float", fieldname: "cut_sheet_l", label: "Cut Sheet Length (L)", reqd: 1 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Float", fieldname: "cut_sheet_w", label: "Cut Sheet Width (W)", reqd: 1 },
					{ fieldtype: "Section Break", label: "Cuts & Ups" },
					{ fieldtype: "Int", fieldname: "no_of_cuts", label: "No of Cuts", default: 2, reqd: 1 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Int", fieldname: "no_of_ups", label: "No of Ups", default: 4, reqd: 1 },
					{ fieldtype: "Section Break", label: "Colors" },
					{ fieldtype: "Int", fieldname: "no_of_colors", label: "No of Colors", default: ci.colour || 0 },
				]);
			}

			// Qty + description — common to both
			fields = fields.concat([
				{ fieldtype: "Section Break", label: "Quantity" },
				{
					fieldtype: "Float", fieldname: "item_qty",
					label: "Item Qty", default: ci.item_qty || 0,
					description: "Auto-filled from Cost Item",
				},
				{ fieldtype: "Section Break" },
				{
					fieldtype: "Data", fieldname: "description",
					label: "Description (optional)", description: "e.g. Cover Page, Inner Page",
				},
			]);

			var d = new frappe.ui.Dialog({
				title: "Add Calculation — " + (ci.cost_item_name || item_name),
				fields: fields,
				primary_action_label: "Create & Open Calculator",
				primary_action: function (vals) {
					d.hide();
					create_calc_breakdown_and_open(frm, cdt, cdn, item_name, ci, vals, pricingType);
				},
			});

			d.show();
		},
	});
}


// ─────────────────────────────────────────────────────────────
//  CREATE Calculation Breakdown → link to Cost Item → open calculator
// ─────────────────────────────────────────────────────────────

function create_calc_breakdown_and_open(frm, cdt, cdn, item_name, ci, vals, pricingType) {

	frappe.show_alert({ message: "Creating Calculation Breakdown…", indicator: "blue" });

	var cbDoc = {
		doctype: "Calculation Breakdown",
		customer_name: frm.doc.customer_name || "",
		ref: frm.doc.inquiry || "",
		pricing_type: pricingType || "Offset",
		base_material: vals.base_material,
		item_qty: vals.item_qty,
		no_of_colors: vals.no_of_colors,
	};

	if (pricingType === "Flexo") {
		// Flexo-specific fields have no dedicated CB columns — they are stored only
		// in ui_state by the calculator. Nothing extra to add to cbDoc here.
	} else {
		// Offset: sheet dimension fields exist on the CB doctype
		cbDoc.full_sheet_l = vals.full_sheet_l;
		cbDoc.full_sheet_w = vals.full_sheet_w;
		cbDoc.cut_sheet_l  = vals.cut_sheet_l;
		cbDoc.cut_sheetw   = vals.cut_sheet_w; // intentional fieldname (no underscore)
		cbDoc.no_of_cuts   = vals.no_of_cuts;
		cbDoc.no_of_ups    = vals.no_of_ups;
	}

	// 1. Create the Calculation Breakdown with header params
	frappe.call({
		method: "frappe.client.insert",
		args: { doc: cbDoc },
		callback: function (r) {
			if (!r.message) return;
			var cb_name = r.message.name;

			// 2. Add to Cost Item calculations child table
			frappe.call({
				method: "frappe.client.get",
				args: { doctype: "cost Item", name: item_name },
				callback: function (r2) {
					if (!r2.message) return;
					var doc = r2.message;
					if (!doc.calculations) doc.calculations = [];
					doc.calculations.push({
						doctype: "Cost Item Calculation",
						calculation_breakdown: cb_name,
						description: vals.description || "",
						unit_cost: 0,
						amount: 0,
					});

					frappe.call({
						method: "frappe.client.save",
						args: { doc: doc },
						callback: function () {
							frappe.show_alert({ message: "Created: " + cb_name, indicator: "green" });

							// 3. Open calculator — pass pricing_type + operations for auto-selection
							var url = "/app/offset-calculator?ref=" + encodeURIComponent(cb_name)
								+ "&cost_sheet=" + encodeURIComponent(frm.doc.name)
								+ "&pricing_type=" + encodeURIComponent(pricingType || "Offset")
								+ get_operations_param(frm);
							window.location.href = url;
						},
					});
				},
			});
		},
	});
}


// ─────────────────────────────────────────────────────────────
//  REFRESH PRICES — reads unit_cost from all Cost Item calculations
//  Updates unit_price, selling_unit_price, amounts on every row
// ─────────────────────────────────────────────────────────────

function refresh_all_prices(frm) {
	var rows = frm.doc.pricing_list || [];
	if (!rows.length) {
		frappe.msgprint({ message: "No items in Pricing List.", indicator: "orange" }); return;
	}
	frappe.show_alert({ message: "Refreshing prices…", indicator: "blue" });
	rows.forEach(function (row) {
		refresh_row_prices(frm, row.doctype, row.name);
	});
}

function refresh_row_prices(frm, cdt, cdn) {
	var row = locals[cdt][cdn];
	if (!row.item) return;

	// Save the Cost Item first (triggers validate → syncs unit costs from CBs)
	// Then read back the updated unit_cost
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "cost Item", name: row.item },
		callback: function (r) {
			if (!r.message) return;
			frappe.call({
				method: "frappe.client.save",
				args: { doc: r.message },
				callback: function (r2) {
					if (!r2.message) return;
					var unit_cost = flt_v(r2.message.unit_cost);
					finish_refresh(frm, cdt, cdn, unit_cost);
				},
			});
		},
	});
}

function finish_refresh(frm, cdt, cdn, unit_cost_sum) {
	var row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "unit_price", round2(unit_cost_sum));

	var unit = round2(unit_cost_sum);
	var qty = flt_v(row.qty);
	var sscl_amt = row.sscl ? round2(unit * 0.0225) : 0;
	var vat_base = unit + sscl_amt;
	var vat_amt = row.vat ? round2(vat_base * 0.18) : 0;
	var sell_unit = round2(unit + sscl_amt + vat_amt);

	frappe.model.set_value(cdt, cdn, "selling_unit_price", sell_unit);
	frappe.model.set_value(cdt, cdn, "ammount", round2(qty * unit));
	frappe.model.set_value(cdt, cdn, "selling_ammount", round2(qty * sell_unit));

	frm.refresh_field("pricing_list");
	render_panel(frm, cdt, cdn);
}


// ─────────────────────────────────────────────────────────────
//  CREATE BREAKDOWN (Book popup / single component)
//  — same logic as before, kept intact
// ─────────────────────────────────────────────────────────────

function run_create_breakdown(frm) {
	var inquiry_name = frm.doc.inquiry;
	if (!inquiry_name) {
		frappe.msgprint({ title: "No Inquiry", message: "Link an Inquiry first.", indicator: "orange" });
		return;
	}
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "Opportunity", name: inquiry_name },
		callback: function (r) {
			if (!r.message) return;
			var opp = r.message;
			var has_pages = cint_v(opp.custom_has_innter_page);
			var pages = opp.custom_pages || [];
			var breakdowns = opp.custom_breakdown || [];
			var subject = (opp.custom_subject || opp.name || "").trim();
			if (has_pages && pages.length > 0) {
				show_page_popup(frm, opp, subject, pages, breakdowns);
			} else {
				create_single(frm, opp, subject, breakdowns);
			}
		},
	});
}

function show_page_popup(frm, opp, subject, pages, breakdowns) {
	var total_bd_qty = breakdowns.reduce(function (s, b) { return s + flt_v(b.qty); }, 0);
	var fields = [{
		fieldtype: "HTML",
		options: "<div style='padding:8px 12px;background:#f0f4ff;border-radius:5px;"
			+ "font-size:12px;color:#1a3a5c;margin-bottom:8px'>"
			+ "<b>Select pages</b> needing individual breakdown calculations.<br><br>"
			+ "✅ <b>Selected</b> → one Cost Item per breakdown row "
			+ "(qty = breakdown qty × no of pages)<br>"
			+ "☐ <b>Not selected</b> → one Cost Item per page row "
			+ "(qty = no of pages × total breakdown qty = <b>" + total_bd_qty.toLocaleString() + "</b>)</div>",
	}];
	pages.forEach(function (page, i) {
		var idx = page.idx || (i + 1);
		fields.push({
			fieldtype: "Check", fieldname: "sel_page_" + idx, default: 0,
			label: "#" + idx + "  " + (page.page_type || "Page")
				+ "  |  " + flt_v(page.no_of_pages) + " pages"
				+ "  |  " + cint_v(page.colour) + " colors"
				+ (page.material ? "  |  " + page.material : ""),
		});
	});
	fields.push({
		fieldtype: "HTML", fieldname: "preview_html",
		options: "<div id='cs-preview' style='margin-top:10px'></div>"
	});

	var d = new frappe.ui.Dialog({
		title: "Select Pages for Breakdown",
		fields: fields,
		primary_action_label: "Create Cost Items",
		primary_action: function (values) {
			d.hide();
			var selected = new Set();
			pages.forEach(function (page, i) {
				if (values["sel_page_" + (page.idx || (i + 1))]) selected.add(page.idx || (i + 1));
			});
			do_create_items(frm, opp, subject, pages, breakdowns, selected, total_bd_qty);
		},
	});
	d.show();
	d.$wrapper.on("change", "input[type=checkbox]", function () {
		var sel = new Set();
		pages.forEach(function (page, i) {
			if (d.get_value("sel_page_" + (page.idx || (i + 1)))) sel.add(page.idx || (i + 1));
		});
		render_preview(d, subject, pages, breakdowns, sel, total_bd_qty);
	});
}

function create_single(frm, opp, subject, breakdowns) {
	// Non-book items also use the breakdown list popup
	// Each breakdown row creates one Cost Item: Subject - BreakdownDescription
	// If no breakdowns, create directly with qty = 0
	if (!breakdowns.length || (breakdowns.length === 1 && !breakdowns[0].description && !breakdowns[0].qty)) {
		var qty = breakdowns.length > 0 ? flt_v(breakdowns[0].qty) : 0;
		frappe.confirm(
			"Create cost item <b>" + subject + "</b><br>Qty: <b>" + qty.toLocaleString() + "</b>",
			function () {
				insert_cost_item(frm, opp.name, {
					cost_item_name: subject, subject: subject, page_type: "",
					colour: cint_v(opp.custom_colour), material: "", item_qty: qty,
					no_of_pages: 0, breakdown: "", is_selected: 0,
				});
			}
		);
		return;
	}

	// Has breakdown rows → show selection popup
	show_single_breakdown_popup(frm, opp, subject, breakdowns);
}

function show_single_breakdown_popup(frm, opp, subject, breakdowns) {
	var fields = [{
		fieldtype: "HTML",
		options: "<div style='padding:8px 12px;background:#f0f4ff;border-radius:5px;"
			+ "font-size:12px;color:#1a3a5c;margin-bottom:8px'>"
			+ "Select breakdown items to create Cost Items for.<br>"
			+ "Each selected row creates: <b>" + subject + " - [Description]</b></div>",
	}];

	breakdowns.forEach(function (bd, i) {
		fields.push({
			fieldtype: "Check",
			fieldname: "sel_bd_" + i,
			label: (bd.description || "(no description)")
				+ "  |  Qty: " + flt_v(bd.qty).toLocaleString()
				+ (bd.order_no ? "  |  Order: " + bd.order_no : ""),
			default: 1,
		});
	});

	fields.push({
		fieldtype: "HTML", fieldname: "preview_html",
		options: "<div id='cs-single-preview' style='margin-top:10px'></div>"
	});

	var d = new frappe.ui.Dialog({
		title: "Create Cost Items — " + subject,
		fields: fields,
		primary_action_label: "Create Cost Items",
		primary_action: function (values) {
			d.hide();
			var selected_bds = breakdowns.filter(function (bd, i) {
				return values["sel_bd_" + i];
			});
			if (!selected_bds.length) {
				frappe.msgprint({ message: "No breakdowns selected.", indicator: "orange" }); return;
			}
			// Sequential insertion for non-book breakdowns
			function insert_bd_next(idx) {
				if (idx >= selected_bds.length) {
					frm.refresh_field("pricing_list");
					frm.save();
					frappe.show_alert({ message: selected_bds.length + " cost items created.", indicator: "green" });
					return;
				}
				var bd = selected_bds[idx];
				var bd_desc = (bd.description || "").trim();
				var name = bd_desc ? subject + " - " + bd_desc : subject;
				var qty = flt_v(bd.qty);
				frappe.call({
					method: "frappe.client.insert",
					args: {
						doc: {
							doctype: "cost Item", cost_item_name: name,
							inquiry: opp.name, subject: subject, page_type: "",
							colour: cint_v(opp.custom_colour), material: "",
							item_qty: qty, no_of_pages: 0,
							breakdown: bd_desc, is_selected: 1,
						}
					},
					callback: function (r) {
						if (r.message) {
							var row = frm.add_child("pricing_list");
							row.item = r.message.name;
							row.item_name = name;
							row.qty = qty;
						}
						insert_bd_next(idx + 1);
					},
					error: function () { insert_bd_next(idx + 1); },
				});
			}
			insert_bd_next(0);
		},
	});

	// Live preview
	d.$wrapper.on("change", "input[type=checkbox]", function () {
		var sel = breakdowns.filter(function (bd, i) { return d.get_value("sel_bd_" + i); });
		var rows = sel.map(function (bd) {
			var bd_desc = (bd.description || "").trim();
			return "<tr><td style='padding:4px 8px;font-weight:600;font-size:12px'>"
				+ (bd_desc ? subject + " - " + bd_desc : subject) + "</td>"
				+ "<td style='padding:4px 8px;text-align:right;font-family:monospace'>"
				+ flt_v(bd.qty).toLocaleString() + "</td></tr>";
		}).join("");
		d.$wrapper.find("#cs-single-preview").html(
			rows ? "<table style='width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-radius:4px'>"
				+ "<thead><tr style='background:#1a3a5c'>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:11px;text-align:left'>Cost Item Name</th>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:11px;text-align:right'>Qty</th>"
				+ "</tr></thead><tbody>" + rows + "</tbody></table>" : ""
		);
	});

	d.show();
	// Trigger initial preview
	d.$wrapper.find("input[type=checkbox]").trigger("change");
}

function build_items(subject, pages, breakdowns, selected, total_bd_qty) {
	var items = [];
	pages.forEach(function (page, i) {
		var idx = page.idx || (i + 1), page_type = page.page_type || "Page";
		var n_pages = flt_v(page.no_of_pages), colour = cint_v(page.colour), material = page.material || "";
		if (selected.has(idx)) {
			breakdowns.forEach(function (bd) {
				var bd_desc = (bd.description || "").trim();
				var name = bd_desc ? subject + " - " + page_type + " - " + bd_desc : subject + " - " + page_type;
				items.push({
					cost_item_name: name, page_type: page_type, colour: colour,
					material: material, item_qty: flt_v(bd.qty) * n_pages, no_of_pages: n_pages,
					breakdown: bd_desc, bd_qty: flt_v(bd.qty), is_selected: 1,
					_calc: "bd_qty(" + flt_v(bd.qty).toLocaleString() + ") × pages(" + n_pages + ")"
				});
			});
		} else {
			items.push({
				cost_item_name: subject + " - " + page_type + " - " + colour + "C",
				page_type: page_type, colour: colour, material: material,
				item_qty: n_pages * total_bd_qty, no_of_pages: n_pages,
				breakdown: "", bd_qty: total_bd_qty, is_selected: 0,
				_calc: "pages(" + n_pages + ") × total_bd(" + total_bd_qty.toLocaleString() + ")"
			});
		}
	});
	return items;
}

function do_create_items(frm, opp, subject, pages, breakdowns, selected, total_bd_qty) {
	var items = build_items(subject, pages, breakdowns, selected, total_bd_qty);
	if (!items.length) { frappe.msgprint({ message: "No items.", indicator: "orange" }); return; }
	frappe.show_alert({ message: "Creating " + items.length + " cost item(s)…", indicator: "blue" });

	// Sequential insertion — each item is inserted AFTER the previous one completes
	// This ensures all child rows are added before frm.save() is called
	function insert_next(idx) {
		if (idx >= items.length) {
			// All inserted — refresh and save once
			frm.refresh_field("pricing_list");
			frm.save();
			frappe.show_alert({ message: items.length + " cost item(s) created.", indicator: "green" });
			return;
		}
		var ci = items[idx];
		frappe.call({
			method: "frappe.client.insert",
			args: {
				doc: {
					doctype: "cost Item",
					cost_item_name: ci.cost_item_name,
					inquiry: opp.name,
					subject: subject,
					page_type: ci.page_type,
					colour: ci.colour,
					material: ci.material,
					item_qty: ci.item_qty,
					no_of_pages: ci.no_of_pages,
					breakdown: ci.breakdown,
					is_selected: ci.is_selected,
				}
			},
			callback: function (r) {
				if (r.message) {
					var row = frm.add_child("pricing_list");
					row.item = r.message.name;
					row.item_name = ci.cost_item_name;
					row.qty = ci.item_qty;
				}
				// Insert next item in sequence
				insert_next(idx + 1);
			},
			error: function () {
				// Continue even on error so other items are still created
				insert_next(idx + 1);
			},
		});
	}
	insert_next(0);
}

function insert_cost_item(frm, inquiry_name, data, cb) {
	frappe.call({
		method: "frappe.client.insert",
		args: { doc: Object.assign({ doctype: "cost Item", inquiry: inquiry_name }, data) },
		callback: function (r) {
			if (!r.message) return;
			var row = frm.add_child("pricing_list");
			row.item = r.message.name; row.item_name = data.cost_item_name; row.qty = data.item_qty || 0;
			frm.refresh_field("pricing_list"); frm.save();
			frappe.show_alert({ message: "Created: " + data.cost_item_name, indicator: "green" });
			if (typeof cb === "function") cb(r.message);
		},
	});
}

function render_preview(d, subject, pages, breakdowns, selected, total_bd_qty) {
	var items = build_items(subject, pages, breakdowns, selected, total_bd_qty);
	if (!items.length) { d.$wrapper.find("#cs-preview").html(""); return; }
	var rows = items.map(function (ci) {
		var tag = ci.is_selected
			? "<span style='background:#dcfce7;color:#166534;border-radius:3px;padding:1px 6px;font-size:10px;font-weight:600'>per-breakdown</span>"
			: "<span style='background:#dbeafe;color:#1e40af;border-radius:3px;padding:1px 6px;font-size:10px;font-weight:600'>per-page</span>";
		return "<tr style='border-bottom:1px solid #f0f0f0'>"
			+ "<td style='padding:5px 8px;font-size:11.5px;font-weight:600'>" + ci.cost_item_name + "</td>"
			+ "<td style='padding:5px 8px;text-align:right;font-family:monospace'>" + ci.item_qty.toLocaleString() + "</td>"
			+ "<td style='padding:5px 8px;font-size:10.5px;color:#6b7280'>" + ci._calc + "</td>"
			+ "<td style='padding:5px 8px'>" + tag + "</td></tr>";
	}).join("");
	d.$wrapper.find("#cs-preview").html(
		"<div style='font-size:11px;font-weight:700;color:#1a3a5c;margin-bottom:5px'>📋 Preview — " + items.length + " items:</div>"
		+ "<div style='border:1px solid #e5e7eb;border-radius:5px;overflow:hidden'>"
		+ "<table style='width:100%;border-collapse:collapse'>"
		+ "<thead><tr style='background:#1a3a5c'>"
		+ "<th style='padding:6px 8px;color:#fff;font-size:11px;text-align:left'>Cost Item Name</th>"
		+ "<th style='padding:6px 8px;color:#fff;font-size:11px;text-align:right'>Qty</th>"
		+ "<th style='padding:6px 8px;color:#fff;font-size:11px;text-align:left'>Calculation</th>"
		+ "<th style='padding:6px 8px;color:#fff;font-size:11px;text-align:left'>Type</th>"
		+ "</tr></thead><tbody>" + rows + "</tbody></table></div>"
	);
}


// ─────────────────────────────────────────────────────────────
//  HELPERS
// ─────────────────────────────────────────────────────────────

function get_operations_param(frm) {
	var names = (frm.doc.operations || [])
		.filter(function (r) { return r.operation; })
		.map(function (r) { return r.operation; });
	// Use | as separator so operation names containing commas don't break the split
	return names.length ? "&operations=" + encodeURIComponent(names.join("|")) : "";
}

function price_row(label, val, bg) {
	return "<tr style='background:" + bg + "'>"
		+ "<td style='padding:5px 10px;font-size:12px;color:#555'>" + label + "</td>"
		+ "<td style='padding:5px 10px;text-align:right;font-family:monospace;font-weight:600;font-size:12px'>"
		+ "LKR " + cur_fmt(val) + "</td></tr>";
}

function cur_fmt(v) {
	return parseFloat(v || 0).toLocaleString("en-LK", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function flt_v(v) { return parseFloat(v || 0) || 0; }
function cint_v(v) { return parseInt(v || 0) || 0; }
function round2(v) { return Math.round((flt_v(v) + Number.EPSILON) * 100) / 100; }