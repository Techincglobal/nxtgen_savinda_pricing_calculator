// Copyright (c) 2026, Techincglobal.com and contributors
// For license information, please see license.txt

// ============================================================
//  Calculation Breakdown — Client Script
//
//  What this does:
//   1. Auto-calculates sheet requirements when header fields change
//   2. Renders Cost Fact HTML (items dropdown + attribute inputs)
//      inside each expanded Cost Fact Details row
//   3. Fetches item rate from Price List → valuation_rate fallback
//   4. Saves selected_item + attribute_json into hidden fields
//   5. amount = req_qty × rate (real-time)
//   6. Renders final pricing summary in the Summary section
// ============================================================


// ── Cost Fact master cache (avoid repeated API calls) ────────
var CF_CACHE = {};

function cf_get_master(name, callback) {
	if (CF_CACHE[name]) { callback(CF_CACHE[name]); return; }
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "Cost Fact", name: name },
		callback: function (r) {
			if (r.message) { CF_CACHE[name] = r.message; callback(r.message); }
		}
	});
}

// ── Debounce ─────────────────────────────────────────────────
function cf_debounce(fn, ms) {
	var t;
	return function () {
		var args = arguments, ctx = this;
		clearTimeout(t);
		t = setTimeout(function () { fn.apply(ctx, args); }, ms);
	};
}


// ============================================================
//  SHEET REQUIREMENT CALCULATIONS
//  Formula from Excel:
//   cut_sheet_ups    = floor(no_of_ups / no_of_cuts)
//   cut_sheet_qty    = ceil(item_qty / cut_sheet_ups)
//   wastage          = max(ceil(cut_sheet_qty * 0.05), 500)
//   req_cut_sheets   = cut_sheet_qty + wastage
//   full_sheet_qty   = ceil(req_cut_sheets / no_of_cuts)
// ============================================================

function calc_sheet_requirements(frm) {

	var no_cuts = Math.max(frm.doc.no_of_cuts || 1, 1);
	var no_ups = Math.max(frm.doc.no_of_ups || 1, 1);
	var item_qty = frm.doc.item_qty || 0;

	if (!item_qty) return;

	var cut_sheet_ups = Math.max(Math.floor(no_ups / no_cuts), 1);
	var cut_sheet_qty = Math.ceil(item_qty / cut_sheet_ups);
	var wastage = Math.max(Math.ceil(cut_sheet_qty * 0.05), 500);
	var req_cut_sheets = cut_sheet_qty + wastage;
	var full_sheet_qty = Math.ceil(req_cut_sheets / no_cuts);

	frappe.model.set_value(frm.doctype, frm.docname, "cut_sheet_ups", cut_sheet_ups);
	frappe.model.set_value(frm.doctype, frm.docname, "cut_sheet_qty", cut_sheet_qty);
	frappe.model.set_value(frm.doctype, frm.docname, "wastage", wastage);
	frappe.model.set_value(frm.doctype, frm.docname, "req_cut_sheets", req_cut_sheets);
	frappe.model.set_value(frm.doctype, frm.docname, "full_sheet_qty", full_sheet_qty);
}

var schedule_sheet_calc = cf_debounce(function (frm) {
	calc_sheet_requirements(frm);
	schedule_summary(frm);
}, 400);


// ============================================================
//  PRICING SUMMARY
//  Total Cost = sum of all cost_facts amounts
//  unit_cost  = total / item_qty
//  sscl       = unit_cost × 2.5%  (if enabled)
//  quoted     = (unit_cost + sscl) × (1 + profit_margin%)
//  vat        = quoted × 18%       (if enabled)
//  selling    = (quoted + vat) × item_qty
// ============================================================

function render_summary(frm) {

	var rows = frm.doc.cost_facts || [];
	var item_qty = frm.doc.item_qty || 0;
	var pm_pct = frappe.utils.flt(frm.doc.profit_margin) / 100;

	// Group totals
	var mat_total = 0;
	var prep_total = 0;
	var prod_total = 0;
	var other = 0;

	rows.forEach(function (r) {
		var amt = frappe.utils.flt(r.amount);
		var grp = (r.cost_group || "").toLowerCase();
		if (grp === "material") mat_total += amt;
		else if (grp === "preparation") prep_total += amt;
		else if (grp === "production") prod_total += amt;
		else other += amt;
	});

	var grand = mat_total + prep_total + prod_total + other;
	var uc = item_qty ? grand / item_qty : 0;
	var sscl = frm.doc.tax_sscl ? uc * 0.025 : 0;
	var quoted = (uc + sscl) * (1 + pm_pct);
	var vat = frm.doc.tax_vat ? quoted * 0.18 : 0;
	var sell_u = quoted + vat;
	var sell_t = sell_u * item_qty;
	var contrib = (quoted * item_qty) > 0
		? (((quoted * item_qty) - (prep_total + mat_total)) / (quoted * item_qty) * 100)
		: 0;

	// Save key values into form fields
	frappe.model.set_value(frm.doctype, frm.docname, "unit_cost", frappe.utils.flt(uc, 4));
	frappe.model.set_value(frm.doctype, frm.docname, "selling_price", frappe.utils.flt(sell_t, 2));

	// Format helper
	var fmt = function (v) {
		return frappe.utils.flt(v, 2).toLocaleString("en-LK", {
			minimumFractionDigits: 2, maximumFractionDigits: 2
		});
	};
	var fmtN = function (v) { return Math.round(v).toLocaleString("en-LK"); };
	var pm_lbl = frappe.utils.flt(frm.doc.profit_margin);

	// Cost breakdown rows
	var cost_rows = [
		["Material", "Material", fmt(mat_total)],
		["Preparation", "Preparation", fmt(prep_total)],
		["Production", "Production", fmt(prod_total)],
	];
	if (other) cost_rows.push(["Other", "Other", fmt(other)]);

	var cost_html = cost_rows.map(function (r) {
		return "<tr><td>" + r[0] + "</td><td style='color:#6b7280;font-size:11px'>" + r[1] +
			"</td><td style='text-align:right;font-family:monospace;font-weight:600'>" + r[2] + "</td></tr>";
	}).join("");

	// Pricing rows
	var price_rows = [
		["Unit Cost", fmt(uc), fmt(grand)]
	];
	if (frm.doc.tax_sscl) price_rows.push(["SSCL (2.5%)", fmt(sscl), fmt(sscl * item_qty)]);
	price_rows.push(["Quoted (" + pm_lbl + "% margin)", fmt(quoted), fmt(quoted * item_qty)]);
	if (frm.doc.tax_vat) price_rows.push(["VAT (18%)", fmt(vat), fmt(vat * item_qty)]);

	var price_html = price_rows.map(function (r) {
		return "<tr><td>" + r[0] + "</td>" +
			"<td style='text-align:right;font-family:monospace'>" + r[1] + "</td>" +
			"<td style='text-align:right;font-family:monospace;font-weight:600'>" + r[2] + "</td></tr>";
	}).join("");

	var html = `
		<style>
			.cb-tbl{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}
			.cb-tbl th{background:#1a3a5c;color:#fff;padding:7px 12px;text-align:left;font-size:11px;letter-spacing:.04em}
			.cb-tbl th.r{text-align:right}
			.cb-tbl td{padding:6px 12px;border-bottom:1px solid #f0f0f0}
			.cb-tbl tr:nth-child(even) td{background:#f8fafc}
			.cb-tot td{background:#1a3a5c!important;color:#fff!important;font-weight:700}
			.cb-badge{display:inline-block;background:#28a745;color:#fff;border-radius:4px;padding:5px 16px;font-size:14px;font-weight:700;margin-top:12px}
		</style>
		<table class="cb-tbl">
			<thead><tr><th>Cost Group</th><th></th><th class="r">Amount (LKR)</th></tr></thead>
			<tbody>
				${cost_html}
				<tr class="cb-tot">
					<td colspan="2"><strong>TOTAL COST</strong></td>
					<td style="text-align:right;font-family:monospace">${fmt(grand)}</td>
				</tr>
			</tbody>
		</table>
		<table class="cb-tbl" style="margin-top:12px">
			<thead>
				<tr>
					<th>Pricing (Qty: ${fmtN(item_qty)})</th>
					<th class="r">Per Unit</th>
					<th class="r">Total</th>
				</tr>
			</thead>
			<tbody>
				${price_html}
				<tr class="cb-tot">
					<td><strong>Selling Price</strong></td>
					<td style="text-align:right;font-family:monospace">${fmt(sell_u)}</td>
					<td style="text-align:right;font-family:monospace">${fmt(sell_t)}</td>
				</tr>
			</tbody>
		</table>
		<div style="margin-top:10px;font-size:12px;color:#6b7280">
			Material Contribution: <strong>${frappe.utils.flt(contrib, 1)}%</strong>
		</div>
		<span class="cb-badge">Unit Sell Price: LKR ${fmt(sell_u)}</span>
	`;

	var $summary = frm.get_field("pricing_summary").$wrapper;
	if ($summary) $summary.html(html);
}

var schedule_summary = cf_debounce(function (frm) {
	render_summary(frm);
}, 350);


// ============================================================
//  RENDER HTML INSIDE COST FACT DETAILS ROW
//  Called on form_render (row expanded) and cost_fact change
// ============================================================

function render_cost_fact_html(frm, cdt, cdn) {

	var row = locals[cdt][cdn];
	if (!row.cost_fact) return;

	cf_get_master(row.cost_fact, function (cf) {

		// Build HTML
		var html = `<div style="padding:10px 0">`;

		// ── Items section ──────────────────────────────────
		if (cf.items && cf.items.length > 1) {

			// Multiple items → dropdown
			var cur = row.selected_item || "";
			var opts = cf.items.map(function (d) {
				var price_info = d.is_fix_rate ? " (Fixed: LKR " + frappe.utils.flt(d.rate).toLocaleString() + ")" : "";
				return `<option value="${d.item}" ${d.item === cur ? "selected" : ""}>${d.item}${price_info}</option>`;
			}).join("");

			html += `
				<div style="margin-bottom:10px">
					<label style="font-size:11px;font-weight:600;color:#6b7280;display:block;margin-bottom:4px">
						SELECT ITEM
					</label>
					<select class="cf-item-select form-control" style="max-width:320px">
						<option value="">— Select —</option>
						${opts}
					</select>
				</div>`;

		} else if (cf.items && cf.items.length === 1) {

			// Single item → show as label, auto-set
			var d = cf.items[0];
			html += `
				<div style="margin-bottom:10px;padding:6px 10px;background:#f0f4ff;border-radius:4px;font-size:13px">
					<span style="color:#6b7280;font-size:11px">ITEM</span>
					<span style="font-weight:600;margin-left:8px">${d.item}</span>
					${d.is_fix_rate ? `<span style="margin-left:8px;color:#28a745;font-size:12px">Fixed Rate: LKR ${frappe.utils.flt(d.rate).toLocaleString()}</span>` : ""}
				</div>`;

			// Auto-set on render
			frappe.model.set_value(cdt, cdn, "selected_item", d.item);
			if (d.is_fix_rate && d.rate) {
				frappe.model.set_value(cdt, cdn, "rate", frappe.utils.flt(d.rate));
				cf_recalc_amount(cdt, cdn);
			} else {
				// Fetch from price list
				cf_fetch_rate(frm, cdt, cdn, d.item);
			}
		}

		// ── Attributes section ─────────────────────────────
		if (cf.table_acwl && cf.table_acwl.length) {

			// Restore saved attribute values
			var saved = {};
			try { saved = JSON.parse(row.attribute_json || "{}"); } catch (e) { }

			html += `
				<div style="margin-top:8px">
					<label style="font-size:11px;font-weight:600;color:#6b7280;display:block;margin-bottom:6px">
						ATTRIBUTES
					</label>
					<div style="display:flex;flex-wrap:wrap;gap:12px">`;

			cf.table_acwl.forEach(function (attr) {
				var val = saved[attr.attribute_name] || "";
				var input_type = (attr.type || "").toLowerCase() === "number" ? "number" : "text";
				html += `
					<div style="display:flex;flex-direction:column;min-width:100px;flex:1">
						<label style="font-size:11px;color:#6b7280;margin-bottom:3px">${attr.lable || attr.attribute_name}</label>
						<input
							type="${input_type}"
							class="cf-attr-input form-control"
							data-attr="${attr.attribute_name}"
							value="${val}"
							min="0"
							style="height:28px;font-size:13px"
						/>
					</div>`;
			});

			html += `</div></div>`;
		}

		// Info text if no items and no attributes
		if ((!cf.items || !cf.items.length) && (!cf.table_acwl || !cf.table_acwl.length)) {
			if (cf.calculation) {
				html += `<div style="font-size:12px;color:#6b7280;font-style:italic;padding:4px 0">ℹ️ ${cf.calculation}</div>`;
			}
		}

		html += `</div>`;

		// ── Inject into HTML field after grid_form renders ─
		setTimeout(function () {

			var grid_row = frm.fields_dict.cost_facts.grid.grid_rows_by_docname[cdn];
			if (!grid_row || !grid_row.grid_form) return;

			var html_field = grid_row.grid_form.fields_dict.html_dlhm;
			if (!html_field || !html_field.wrapper) return;

			var $w = $(html_field.wrapper);
			$w.html(html);

			// ── Item select change ─────────────────────────
			$w.off("change.cf_item").on("change.cf_item", ".cf-item-select", function () {
				var item = $(this).val();
				frappe.model.set_value(cdt, cdn, "selected_item", item);
				if (item) {
					// Check if fixed rate
					var sel_item = (cf.items || []).find(function (d) { return d.item === item; });
					if (sel_item && sel_item.is_fix_rate && sel_item.rate) {
						frappe.model.set_value(cdt, cdn, "rate", frappe.utils.flt(sel_item.rate));
						cf_recalc_amount(cdt, cdn);
						schedule_summary(frm);
					} else {
						cf_fetch_rate(frm, cdt, cdn, item);
					}
				}
			});

			// ── Attribute input change ─────────────────────
			var dAttr = cf_debounce(function () {
				var values = {};
				$w.find(".cf-attr-input").each(function () {
					values[$(this).data("attr")] = $(this).val();
				});
				frappe.model.set_value(cdt, cdn, "attribute_json", JSON.stringify(values));
			}, 300);

			$w.off("input.cf_attr").on("input.cf_attr", ".cf-attr-input", dAttr);

		}, 250);
	});
}


// ============================================================
//  FETCH ITEM RATE FROM PRICE LIST → VALUATION RATE FALLBACK
// ============================================================

function cf_fetch_rate(frm, cdt, cdn, item_code) {

	if (!item_code) return;

	var price_list = frm.doc.price_list;

	if (price_list) {
		frappe.call({
			method: "frappe.client.get_value",
			args: {
				doctype: "Item Price",
				filters: { item_code: item_code, price_list: price_list, selling: 1 },
				fieldname: "price_list_rate"
			},
			callback: function (r) {
				var rate = r.message && r.message.price_list_rate;
				if (rate) {
					frappe.model.set_value(cdt, cdn, "rate", frappe.utils.flt(rate));
					cf_recalc_amount(cdt, cdn);
					schedule_summary(frm);
				} else {
					// Fallback: valuation_rate
					cf_fetch_valuation_rate(frm, cdt, cdn, item_code);
				}
			}
		});
	} else {
		cf_fetch_valuation_rate(frm, cdt, cdn, item_code);
	}
}

function cf_fetch_valuation_rate(frm, cdt, cdn, item_code) {
	frappe.call({
		method: "frappe.client.get_value",
		args: {
			doctype: "Item",
			filters: { name: item_code },
			fieldname: "valuation_rate"
		},
		callback: function (r) {
			var rate = r.message && r.message.valuation_rate;
			frappe.model.set_value(cdt, cdn, "rate", frappe.utils.flt(rate || 0));
			cf_recalc_amount(cdt, cdn);
			schedule_summary(frm);
		}
	});
}


// ============================================================
//  AMOUNT = REQ_QTY × RATE  (uses frappe.model.set_value)
// ============================================================

function cf_recalc_amount(cdt, cdn) {
	var row = locals[cdt][cdn];
	var amount = frappe.utils.flt(row.req_qty) * frappe.utils.flt(row.rate);
	frappe.model.set_value(cdt, cdn, "amount", frappe.utils.flt(amount, 2));
}


// ============================================================
//  FORM EVENTS — Calculation Breakdown
// ============================================================

frappe.ui.form.on("Calculation Breakdown", {

	setup: function (frm) {
		// Auto-resolve price list from Customer when customer changes
		frm.set_query("price_list", function () {
			return { filters: { selling: 1 } };
		});
	},

	refresh: function (frm) {
		render_summary(frm);

		// Add Calculate button in toolbar
		frm.add_custom_button(__("Recalculate"), function () {
			calc_sheet_requirements(frm);
			render_summary(frm);
			frappe.show_alert({ message: "Recalculated", indicator: "green" });
		}, __("Actions"));
	},

	// Header field changes → recalc sheets + summary
	no_of_cuts: function (frm) { schedule_sheet_calc(frm); },
	no_of_ups: function (frm) { schedule_sheet_calc(frm); },
	no_of_colors: function (frm) { schedule_sheet_calc(frm); },
	item_qty: function (frm) { schedule_sheet_calc(frm); },
	profit_margin: function (frm) { schedule_summary(frm); },
	tax_sscl: function (frm) { schedule_summary(frm); },
	tax_vat: function (frm) { schedule_summary(frm); },

	// Price list changed → re-fetch rates for all rows that have items
	price_list: function (frm) {
		(frm.doc.cost_facts || []).forEach(function (row) {
			if (row.cost_fact && row.selected_item) {
				cf_fetch_rate(frm, row.doctype, row.name, row.selected_item);
			}
		});
	},

	// Base material rate auto-fill from price list
	base_material: function (frm) {
		if (!frm.doc.base_material) return;
		var pl = frm.doc.price_list;
		if (pl) {
			frappe.call({
				method: "frappe.client.get_value",
				args: {
					doctype: "Item Price",
					filters: { item_code: frm.doc.base_material, price_list: pl, selling: 1 },
					fieldname: "price_list_rate"
				},
				callback: function (r) {
					var rate = r.message && r.message.price_list_rate;
					if (rate) frappe.model.set_value(frm.doctype, frm.docname, "material_rate", frappe.utils.flt(rate));
				}
			});
		}
	},
});


// ============================================================
//  COST FACT DETAILS — Child Table Events
// ============================================================

frappe.ui.form.on("Cost Fact Details", {

	// Fires when a row is expanded (grid form opened)
	form_render: function (frm, cdt, cdn) {
		render_cost_fact_html(frm, cdt, cdn);
	},

	// Cost fact selected → render HTML panel
	cost_fact: function (frm, cdt, cdn) {
		// Clear previous values
		frappe.model.set_value(cdt, cdn, "selected_item", "");
		frappe.model.set_value(cdt, cdn, "rate", 0);
		frappe.model.set_value(cdt, cdn, "req_qty", 0);
		frappe.model.set_value(cdt, cdn, "amount", 0);
		frappe.model.set_value(cdt, cdn, "attribute_json", "{}");
		delete CF_CACHE[locals[cdt][cdn].cost_fact]; // clear cache so fresh data loads
		render_cost_fact_html(frm, cdt, cdn);
	},

	// Manual rate or qty change → recalc amount
	rate: function (frm, cdt, cdn) {
		cf_recalc_amount(cdt, cdn);
		schedule_summary(frm);
	},

	req_qty: function (frm, cdt, cdn) {
		cf_recalc_amount(cdt, cdn);
		schedule_summary(frm);
	},

	// Row removed → update summary
	cost_facts_remove: function (frm) {
		schedule_summary(frm);
	},
});