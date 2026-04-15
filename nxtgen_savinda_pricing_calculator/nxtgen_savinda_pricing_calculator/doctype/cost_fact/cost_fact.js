// Copyright (c) 2026, Techincglobal.com and contributors
// Cost Fact — Client Script
// Shows a live variable reference panel next to the formula fields
// so the admin can see all available variables while writing formulas.

frappe.ui.form.on("Cost Fact", {
	refresh: function (frm) { render_formula_reference(frm); },
	after_save: function (frm) { render_formula_reference(frm); },
});

frappe.ui.form.on("Cost Fact  Attribute", {
	attribute_name: function (frm) { render_formula_reference(frm); },
	form_render: function (frm) { render_formula_reference(frm); },
	cost_fact__attribute_remove: function (frm) { render_formula_reference(frm); },
});


// ─────────────────────────────────────────────────────────────
//  RENDER VARIABLE REFERENCE PANEL
// ─────────────────────────────────────────────────────────────

function render_formula_reference(frm) {

	// ── Collect user-defined attributes (live from the table) ──
	var user_attrs = (frm.doc.table_acwl || [])
		.filter(function (r) { return r.attribute_name; })
		.map(function (r) {
			return { name: r.attribute_name, label: r.lable || r.attribute_name, type: r.type || "Number" };
		});

	// ── Sheet & Order variables ────────────────────────────────
	var SHEET_VARS = [
		{ name: "full_sheet_qty", desc: "Full sheets required for the job" },
		{ name: "cut_sheet_qty", desc: "Cut sheets required (after wastage is added)" },
		{ name: "cut_sheet_area", desc: "Area of one cut sheet  (cut_sheet_l × cut_sheet_w  sq.in)" },
		{ name: "cut_sheet_ups", desc: "Ups per cut sheet  =  no_of_ups ÷ no_of_cuts" },
		{ name: "no_of_colors", desc: "Number of print colors (from order form)" },
		{ name: "item_qty", desc: "Order quantity — total number of cartons / pieces" },
		{ name: "no_of_cuts", desc: "Number of cuts (from order form)" },
		{ name: "no_of_ups", desc: "Number of ups (from order form)" },
	];

	// ── Rate & Minimum variables ───────────────────────────────
	var RATE_VARS = [
		{
			name: "item_rate",
			desc: "Rate from the Price List for the selected item. Falls back to valuation rate if no price list set.",
			color: "#28a745",
		},
		{
			name: "fix_rate",
			desc: "Fixed rate stored on the Cost Fact Item row (only when Is Fix Rate is ticked).",
			color: "#28a745",
		},
		{
			name: "material_rate",
			desc: "Base material rate from the order form (use for paper / board cost facts).",
			color: "#28a745",
		},
		{
			name: "min_rate",
			desc: "Per-unit minimum from the Item row Min Rate field. Use in rate_formula to enforce a minimum per block, per sheet, etc.  Example: max(attr.length * attr.width * item_rate, min_rate)",
			color: "#dc3545",
		},
		{
			name: "min_qty",
			desc: "The Min Qty value set on this Cost Fact (the field below). Use in rate_formula for per-color minimum.  Example: max(item_rate, min_qty / cut_sheet_qty)",
			color: "#dc3545",
		},
	];

	var MATH_FUNS = [
		{ name: "ceil(x)", desc: "Round UP to nearest integer  —  ceil(2.1) → 3" },
		{ name: "floor(x)", desc: "Round DOWN to nearest integer  —  floor(2.9) → 2" },
		{ name: "round(x, n)", desc: "Round to n decimal places  —  round(1.567, 2) → 1.57" },
		{ name: "max(x, y)", desc: "Return the larger value  —  max(750, 2000) → 2000" },
		{ name: "min(x, y)", desc: "Return the smaller value  —  min(500, 200) → 200" },
		{ name: "abs(x)", desc: "Absolute value (remove negative sign)  —  abs(-5) → 5" },
	];

	// ── HTML helpers ───────────────────────────────────────────
	function badge(text, color) {
		return '<span style="display:inline-block;font-family:monospace;font-size:11px;'
			+ 'font-weight:600;padding:1px 7px;border-radius:3px;background:' + color
			+ ';color:#fff;white-space:nowrap">' + text + '</span>';
	}

	function section_header(title, color) {
		return '<tr><td colspan="2" style="padding:8px 8px 4px;font-size:11px;font-weight:700;'
			+ 'color:' + color + ';text-transform:uppercase;letter-spacing:.06em;'
			+ 'border-top:1px solid #eee">' + title + '</td></tr>';
	}

	// ── Attribute rows (live — updates as user edits table) ────
	var attr_rows = "";
	if (user_attrs.length) {
		attr_rows = user_attrs.map(function (a) {
			return '<tr>'
				+ '<td style="padding:4px 8px;white-space:nowrap">'
				+ badge("attr." + a.name, "#6f42c1")
				+ '&nbsp;<span style="font-family:monospace;font-size:10px;color:#aaa">or</span>&nbsp;'
				+ badge(a.name, "#6f42c1")
				+ '</td>'
				+ '<td style="padding:4px 8px;color:#555;font-size:12px">'
				+ a.label
				+ ' <span style="color:#aaa;font-size:10px">(' + a.type + ' — user enters this)</span>'
				+ '</td></tr>';
		}).join("");
	} else {
		attr_rows = '<tr><td colspan="2" style="padding:4px 8px;color:#aaa;font-size:12px;font-style:italic">'
			+ 'No attributes defined yet. Add rows to the Attributes table above to create user-input variables.'
			+ '</td></tr>';
	}

	// ── Sheet variable rows ────────────────────────────────────
	var sheet_rows = SHEET_VARS.map(function (v) {
		return '<tr>'
			+ '<td style="padding:4px 8px;white-space:nowrap">' + badge(v.name, "#2c7be5") + '</td>'
			+ '<td style="padding:4px 8px;color:#555;font-size:12px">' + v.desc + '</td>'
			+ '</tr>';
	}).join("");

	// ── Rate variable rows (two colours: green=rates, red=minimums) ──
	var rate_rows = RATE_VARS.map(function (v) {
		return '<tr>'
			+ '<td style="padding:4px 8px;white-space:nowrap">' + badge(v.name, v.color) + '</td>'
			+ '<td style="padding:4px 8px;color:#555;font-size:12px">' + v.desc + '</td>'
			+ '</tr>';
	}).join("");

	// ── Math function rows ─────────────────────────────────────
	var math_rows = MATH_FUNS.map(function (v) {
		return '<tr>'
			+ '<td style="padding:4px 8px;white-space:nowrap">' + badge(v.name, "#fd7e14") + '</td>'
			+ '<td style="padding:4px 8px;color:#555;font-size:12px">' + v.desc + '</td>'
			+ '</tr>';
	}).join("");

	// ── Examples table (all 3 minimum cases included) ─────────
	var examples = [
		{
			case: "Die Cutter / Embossing Block",
			qty: "attr.qty",
			rate: "max(attr.length * attr.width * item_rate,  min_rate)",
			notes: "min_rate on item row = per-block minimum (e.g. 3500). "
				+ "2 blocks × max(area×rate, 3500) = minimum 7,000",
			min_field: "Min Rate (on item row)",
		},
		{
			case: "Printing Plates",
			qty: "no_of_colors",
			rate: "item_rate",
			notes: "One plate per color. Rate from price list.",
			min_field: "—",
		},
		{
			case: "Printing Machine",
			qty: "cut_sheet_qty * no_of_colors",
			rate: "max(item_rate,  min_qty / cut_sheet_qty)",
			notes: "min_qty = minimum per color (e.g. 2000). "
				+ "4 colors → total min = 2000×4 = 8,000. "
				+ "Scales automatically for any number of colors.",
			min_field: "Min Qty (on Cost Fact)",
		},
		{
			case: "UV Varnish / Lamination",
			qty: "cut_sheet_qty * cut_sheet_area",
			rate: "item_rate",
			notes: "Total area in sq.in × rate per sq.in.",
			min_field: "—",
		},
		{
			case: "Sorting / Pasting",
			qty: "item_qty",
			rate: "item_rate",
			notes: "Total job minimum — set Min Qty on Cost Fact (e.g. 2000). "
				+ "System uses max(calculated, min_qty) automatically.",
			min_field: "Min Qty (on Cost Fact)",
		},
		{
			case: "Delivery / Guillotine (fixed)",
			qty: "1",
			rate: "fix_rate",
			notes: "Fixed charge per job. Tick Is Fix Rate on item row and enter the amount.",
			min_field: "—",
		},
		{
			case: "Corrugated Boxes",
			qty: "ceil(item_qty / attr.per_box)",
			rate: "item_rate",
			notes: "Attribute per_box = cartons per box. ceil() rounds up to whole boxes.",
			min_field: "—",
		},
	];

	var example_rows = examples.map(function (e, i) {
		var bg = i % 2 === 0 ? "#fffbeb" : "#fffff8";
		return '<tr style="border-bottom:1px solid #ffe69c;background:' + bg + '">'
			+ '<td style="padding:5px 8px;white-space:nowrap;font-weight:600;color:#333;font-size:11.5px">' + e.case + '</td>'
			+ '<td style="padding:5px 8px;font-family:monospace;font-size:11px;color:#1a3a5c">' + e.qty + '</td>'
			+ '<td style="padding:5px 8px;font-family:monospace;font-size:11px;color:#1a3a5c">' + e.rate + '</td>'
			+ '<td style="padding:5px 8px;font-size:10.5px;color:#6b7280">' + e.notes + '</td>'
			+ '<td style="padding:5px 8px;font-size:10.5px;white-space:nowrap">'
			+ (e.min_field !== "—"
				? '<span style="background:#fee2e2;color:#b91c1c;border-radius:3px;padding:1px 5px;font-size:10px;font-weight:600">' + e.min_field + '</span>'
				: '<span style="color:#d1d5db">—</span>')
			+ '</td>'
			+ '</tr>';
	}).join("");

	// ── Legend for minimum fields ──────────────────────────────
	var min_legend = ''
		+ '<div style="margin-top:10px;padding:8px 12px;background:#fff1f2;border:1px solid #fca5a5;border-radius:4px">'
		+ '<div style="font-size:11px;font-weight:700;color:#b91c1c;margin-bottom:5px">🔴 Minimum Fields — which one to use?</div>'
		+ '<table style="font-size:11.5px;width:100%;border-collapse:collapse">'
		+ '<tr>'
		+ '<td style="padding:3px 8px;white-space:nowrap">' + badge("min_rate", "#dc3545") + '&nbsp;on&nbsp;<b>Item row</b></td>'
		+ '<td style="padding:3px 8px;color:#555">Per-unit minimum (per block, per sheet). '
		+ 'Use in <code>rate_formula</code>: <code style="background:#f5f5f5;padding:1px 4px;border-radius:2px">max(calculated_rate, min_rate)</code></td>'
		+ '</tr>'
		+ '<tr>'
		+ '<td style="padding:3px 8px;white-space:nowrap">' + badge("min_qty", "#dc3545") + '&nbsp;on&nbsp;<b>Cost Fact</b></td>'
		+ '<td style="padding:3px 8px;color:#555">Two uses:<br>'
		+ '&nbsp;&nbsp;① <b>Job minimum</b> — system applies automatically after calculation (e.g. Sorting min LKR 2,000)<br>'
		+ '&nbsp;&nbsp;② <b>Per-color minimum</b> — use in rate_formula: '
		+ '<code style="background:#f5f5f5;padding:1px 4px;border-radius:2px">max(item_rate, min_qty / cut_sheet_qty)</code></td>'
		+ '</tr>'
		+ '</table>'
		+ '</div>';

	// ── Assemble full panel HTML ───────────────────────────────
	var html = ''
		+ '<div style="background:#f8fafc;border:1px solid #d0e2ff;border-radius:6px;padding:12px 14px;margin-top:8px;margin-bottom:6px">'

		// Header
		+ '<div style="font-size:12px;font-weight:700;color:#1a3a5c;margin-bottom:10px;'
		+ 'display:flex;align-items:center;gap:8px">'
		+ '📐 Available Variables for Qty Formula &amp; Rate Formula'
		+ '<span style="font-weight:400;color:#888;font-size:11px">— type these exactly in the formula fields below</span>'
		+ '</div>'

		// Variable table
		+ '<table style="width:100%;border-collapse:collapse;font-size:12.5px">'
		+ section_header("① User Attribute Variables  (from Attributes table above)", "#6f42c1")
		+ attr_rows
		+ section_header("② Sheet &amp; Order Variables  (auto-calculated from order form)", "#2c7be5")
		+ sheet_rows
		+ section_header("③ Rate &amp; Minimum Variables  (green = rates,  red = minimums)", "#28a745")
		+ rate_rows
		+ section_header("④ Math Functions", "#fd7e14")
		+ math_rows
		+ '</table>'

		// Minimum legend
		+ min_legend

		// Examples table
		+ '<div style="margin-top:12px;padding:10px 12px;background:#fffbeb;border:1px solid #fcd34d;border-radius:4px">'
		+ '<div style="font-size:11px;font-weight:700;color:#92400e;margin-bottom:7px">📝 Formula Examples (including minimum configurations)</div>'
		+ '<table style="font-size:11.5px;color:#555;width:100%;border-collapse:collapse">'
		+ '<tr style="border-bottom:2px solid #fcd34d;background:#fef3c7">'
		+ '<td style="padding:4px 8px;font-weight:700;color:#333;white-space:nowrap">Cost Item</td>'
		+ '<td style="padding:4px 8px;font-weight:700;color:#333">Qty Formula</td>'
		+ '<td style="padding:4px 8px;font-weight:700;color:#333">Rate Formula</td>'
		+ '<td style="padding:4px 8px;font-weight:700;color:#333">Notes</td>'
		+ '<td style="padding:4px 8px;font-weight:700;color:#333;white-space:nowrap">Min Field</td>'
		+ '</tr>'
		+ example_rows
		+ '</table>'
		+ '</div>'

		+ '</div>';

	// ── Inject into Calculation section ───────────────────────
	var panel_id = "cf-var-reference";
	var $qty_field = frm.get_field("qty_formula");
	if (!$qty_field) return;  // field not yet added to DocType

	var $section_body = $qty_field.$wrapper.closest(".form-section").find(".section-body");
	$section_body.find("#" + panel_id).remove();
	$('<div id="' + panel_id + '"></div>').html(html)
		.insertBefore($qty_field.$wrapper.closest(".frappe-field-group, .form-column, .form-group"));
}