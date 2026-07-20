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

// Costing config — loaded once per form session
var _cc = { sscl_rate: 2.5, vat_rate: 18 };
frappe.call({
	method: "nxtgen_savinda_pricing_calculator.api.offset_calculator.get_costing_config",
	callback: function (r) {
		if (r.message) {
			_cc.sscl_rate = parseFloat(r.message.sscl_rate || 2.5);
			_cc.vat_rate = parseFloat(r.message.vat_rate || 18);
		}
	},
});

frappe.ui.form.on("Cost Sheet", {

	refresh: function (frm) {
		// ── Draft-only actions ─────────────────────────────────
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Create Breakdown"), function () {
				run_create_breakdown(frm);
			}, __("Actions"));
			frm.add_custom_button(__("Refresh Prices"), function () {
				refresh_all_prices(frm);
			}, __("Actions"));

			// Banner for amended cost sheets
			if (frm.doc.amended_from) {
				frm.set_intro(
					"<b>Price Revision</b> — This Cost Sheet is an amendment of <a href='/app/cost-sheet/"
					+ frm.doc.amended_from + "'>" + frm.doc.amended_from + "</a>. "
					+ "All Calculation Breakdowns have been amended. Open each item's calculation panel, "
					+ "make changes, save, then submit this Cost Sheet.",
					"blue"
				);
			}
		}

		// ── NPD Ticket (available once the cost sheet exists) ──
		if (!frm.is_new()) {
			frm.add_custom_button(__("Create NPD Ticket"), function () {
				_npd_create_checked("Cost Sheet", frm.doc.name);
			}, __("Actions"));
		}

		// ── Download cost breakdown CSV (per cost item) ──
		if (!frm.is_new() && (frm.doc.pricing_list || []).length) {
			frm.add_custom_button(__("Download Costing (Excel)"), function () {
				_show_costing_csv_dialog(frm);
			}, __("Actions"));
		}

		// ── Artwork approval (Artwork Approver role) ──────────
		if (!frm.is_new()) {
			var roles = frappe.user_roles || [];
			var can_approve = roles.indexOf("Artwork Approver") >= 0 || roles.indexOf("System Manager") >= 0;
			if (frm.doc.artwork_status) {
				frm.dashboard.set_headline_alert(
					"Artwork status: <b>" + frm.doc.artwork_status + "</b>"
					+ (frm.doc.artwork_approved_by ? " — " + frm.doc.artwork_approved_by : "")
				);
			}
			if (can_approve && frm.doc.artwork_status !== "Approved") {
				frm.add_custom_button(__("Approve Artwork"), function () {
					_artwork_action(frm, "approve_artwork", "Approve Artwork");
				}, __("Artwork"));
			}
			if (can_approve && frm.doc.artwork_status !== "Rejected") {
				frm.add_custom_button(__("Reject Artwork"), function () {
					_artwork_action(frm, "reject_artwork", "Reject Artwork");
				}, __("Artwork"));
			}
		}

		// ── Submitted-only actions ─────────────────────────────
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Create Quotation"), function () {
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

			frm.add_custom_button(__("Print / PDF"), function () {
				var url = "/printview?doctype=Cost+Sheet&name="
					+ encodeURIComponent(frm.doc.name)
					+ "&format=Cost+Sheet+Summary&trigger_print=1&no_letterhead=0";
				var w = window.open(frappe.urllib.get_full_url(url));
				if (!w) frappe.msgprint(__("Please allow pop-ups to open the print view."));
			});

			// ── Pricing totals bar below the grid ──────────────
			setTimeout(function () {
				if (!frm.fields_dict.pricing_list) return;
				var rows = frm.doc.pricing_list || [];
				var total_cost = 0, total_sell = 0;
				rows.forEach(function (r) {
					total_cost += flt_v(r.ammount);
					total_sell += flt_v(r.selling_ammount);
				});
				var $grid = frm.fields_dict.pricing_list.$wrapper;
				$grid.find(".pricing-totals-bar").remove();
				if (rows.length) {
					$grid.append(
						"<div class='pricing-totals-bar' style='display:flex;justify-content:flex-end;"
						+ "gap:24px;padding:8px 16px;margin-top:4px;background:#f0f4ff;"
						+ "border:1px solid #dde4f0;border-radius:4px;font-size:12px'>"
						+ "<span style='color:#555'>Total Cost Amount: "
						+ "<b style='font-family:monospace;color:#1a3a5c'>LKR " + cur_fmt(total_cost) + "</b></span>"
						+ "<span style='color:#555'>Total Selling Amount: "
						+ "<b style='font-family:monospace;color:#166534'>LKR " + cur_fmt(total_sell) + "</b></span>"
						+ "</div>"
					);
				}
			}, 600);
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

				// Copy compliance from inquiry
				// Table MultiSelect requires frappe.model.add_child (same as the control's own parse()),
				// because set_value with plain strings causes row["type"] → undefined in set_formatted_input.
				if (!frm.doc.compliance || !frm.doc.compliance.length) {
					var compliance_vals = (o.custom_compliance || [])
						.map(function (c) { return c.type || ""; })
						.filter(Boolean);
					if (compliance_vals.length) {
						frm.doc.compliance = [];
						compliance_vals.forEach(function (val) {
							var new_row = frappe.model.add_child(frm.doc, "Compliance Details", "compliance");
							new_row.type = val;
						});
						frm.refresh_field("compliance");
					}
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


// Create an NPD ticket, validating materials first. Unlinked materials open a mapping
// table (sample material → pick actual Item) before creating.
function _npd_create_checked(source_type, source_name) {
	var M = "nxtgen_savinda_pricing_calculator.api.job_ticket.create_npd_checked";
	frappe.call({
		method: M, args: { source_type: source_type, source_name: source_name, force: 0 },
		freeze: true, freeze_message: __("Checking materials…"),
		callback: function (r) {
			if (!r.message) return;
			if (r.message.needs_confirm) {
				_npd_material_map_dialog(M, source_type, source_name, r.message.unlinked || []);
			} else if (r.message.job_ticket) {
				frappe.set_route("Form", "Job Ticket", r.message.job_ticket);
			}
		},
	});
}

function _npd_material_map_dialog(method, source_type, source_name, unlinked) {
	var controls = [];
	var d = new frappe.ui.Dialog({
		title: __("Link Materials to Items"),
		size: "large",
		fields: [
			{ fieldtype: "HTML", fieldname: "info", options:
				"<div style='margin-bottom:8px;color:#555;font-size:12px'>These materials are not linked to an Item. "
				+ "Pick the actual Item for each (leave blank to skip — it won't be added to the sample BOM), then Proceed.</div>" },
			{ fieldtype: "HTML", fieldname: "tbl" },
		],
		primary_action_label: __("Proceed & Create NPD"),
		primary_action: function () {
			var map = {};
			controls.forEach(function (c) { var v = c.ctrl.get_value(); if (v) map[c.name] = v; });
			d.hide();
			frappe.call({
				method: method,
				args: { source_type: source_type, source_name: source_name, force: 1, material_map: JSON.stringify(map) },
				freeze: true, freeze_message: __("Creating NPD…"),
				callback: function (r2) {
					if (r2.message && r2.message.job_ticket) frappe.set_route("Form", "Job Ticket", r2.message.job_ticket);
				},
			});
		},
	});
	var $w = d.fields_dict.tbl.$wrapper;
	$w.html("<table class='table table-bordered' style='font-size:12px;margin:0'>"
		+ "<thead><tr><th style='width:45%'>Sample Material</th><th>Actual Item</th></tr></thead><tbody></tbody></table>");
	var $tb = $w.find("tbody");
	(unlinked || []).forEach(function (name, i) {
		var $tr = $("<tr>").appendTo($tb);
		$("<td>").text(name).appendTo($tr);
		var $td = $("<td>").appendTo($tr);
		var ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Item", fieldname: "item_" + i, placeholder: __("Select Item") },
			parent: $td.get(0), render_input: true,
		});
		ctrl.set_value("");
		controls.push({ name: name, ctrl: ctrl });
	});
	d.show();
}

// ── Download cost-breakdown CSV per cost item ─────────────────
function _show_costing_csv_dialog(frm) {
	var rows = (frm.doc.pricing_list || []).filter(function (r) { return r.item; });
	if (!rows.length) { frappe.msgprint(__("No cost items to export.")); return; }
	var d = new frappe.ui.Dialog({
		title: __("Download Cost Breakdown (Excel)"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "tbl" }],
	});
	var html = "<table class='table table-bordered' style='font-size:12px;margin:0'>"
		+ "<thead><tr><th>Cost Item</th><th style='width:110px'></th></tr></thead><tbody>";
	rows.forEach(function (r) {
		html += "<tr><td>" + frappe.utils.escape_html(r.item_name || r.item) + "</td>"
			+ "<td><button class='btn btn-xs btn-default csv-dl' data-item='"
			+ frappe.utils.escape_html(r.item) + "'>⬇ Excel</button></td></tr>";
	});
	html += "</tbody></table>";
	d.fields_dict.tbl.$wrapper.html(html);
	d.fields_dict.tbl.$wrapper.on("click", ".csv-dl", function () {
		var it = $(this).attr("data-item");
		var url = "/api/method/nxtgen_savinda_pricing_calculator.api.offset_calculator.download_cost_breakdown_xlsx"
			+ "?cost_item=" + encodeURIComponent(it);
		window.open(frappe.urllib.get_full_url(url));
	});
	d.show();
}

// ── Artwork approve / reject (with optional remarks) ──────────
function _artwork_action(frm, method, title) {
	var d = new frappe.ui.Dialog({
		title: __(title),
		fields: [{ fieldtype: "Small Text", fieldname: "remarks", label: __("Remarks") }],
		primary_action_label: __(title),
		primary_action: function (v) {
			frappe.call({
				method: "nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.cost_sheet.cost_sheet." + method,
				args: { cost_sheet: frm.doc.name, remarks: v.remarks || "" },
				freeze: true,
				callback: function (r) {
					d.hide();
					if (r.message && r.message.ok) {
						frm.reload_doc();
						frappe.show_alert({ message: "Artwork " + r.message.status, indicator: r.message.status === "Approved" ? "green" : "red" });
					}
				},
			});
		},
	});
	d.show();
}

// ─────────────────────────────────────────────────────────────
//  RECALCULATE row amounts and re-render panel
// ─────────────────────────────────────────────────────────────

function recalc_row(frm, cdt, cdn) {
	var row = locals[cdt][cdn];

	// unit_price is set by refresh_all_prices (sum of CB unit costs)
	var unit = flt_v(row.unit_price);
	var qty = flt_v(row.qty);
	var sscl = unit * (_cc.sscl_rate / 100);
	var sscl_total = row.sscl ? sscl : 0;
	var vat = (unit + sscl_total) * (_cc.vat_rate / 100);
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
	var is_submitted = frm.doc.docstatus >= 1; // 1=submitted, 2=cancelled → both lock the panel

	// Load Cost Item calculations list from DB
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "cost Item", name: item_name },
		callback: function (r) {
			if (!r.message) return;
			var ci = r.message;
			var calcs = ci.calculations || [];
			// When submitted show only completed calculations (unit_cost > 0)
			var display_calcs = is_submitted
				? calcs.filter(function (c) { return flt_v(c.unit_cost) > 0; })
				: calcs;

			// Compute unit_price = sum of all CB unit_costs
			var unit_cost_sum = calcs.reduce(function (s, c) { return s + flt_v(c.unit_cost); }, 0);

			// Update unit_price silently if changed (draft only)
			if (!is_submitted && Math.abs(flt_v(row.unit_price) - unit_cost_sum) > 0.001) {
				frappe.model.set_value(cdt, cdn, "unit_price", round2(unit_cost_sum));
				row = locals[cdt][cdn]; // re-read
			}

			var unit = round2(unit_cost_sum);
			var qty = flt_v(row.qty);
			var margin_pct = flt_v(row.profit_margin);
			var sscl_amt = row.sscl ? round2(unit * (_cc.sscl_rate / 100)) : 0;
			var cost_with_sscl = unit + sscl_amt;
			var margin_amt = margin_pct ? round2(cost_with_sscl * (margin_pct / 100)) : 0;
			var qu = round2(cost_with_sscl + margin_amt);
			var vat_amt = row.vat ? round2(qu * (_cc.vat_rate / 100)) : 0;
			var sell_unit = round2(qu + vat_amt);
			var cost_amt = round2(qty * unit);
			var sell_amt = round2(qty * sell_unit);

			// Build calculation list rows
			var calc_rows = "";
			if (display_calcs.length === 0) {
				var empty_msg = is_submitted
					? "No completed calculations."
					: "No calculations yet. Click + Add to create one.";
				calc_rows = "<tr><td colspan='4' style='padding:8px;color:#aaa;font-style:italic;text-align:center'>"
					+ empty_msg + "</td></tr>";
			} else {
				display_calcs.forEach(function (c) {
					var action_td;
					if (is_submitted) {
						var view_url = "/app/offset-calculator?ref=" + encodeURIComponent(c.calculation_breakdown || "")
							+ "&cost_sheet=" + encodeURIComponent(frm.doc.name)
							+ "&pricing_type=" + encodeURIComponent(frm.doc.pricing_type || "Offset")
							+ "&view_only=1";
						var print_url = "/api/method/frappe.utils.print_format.download_pdf?doctype=Calculation+Breakdown&name="
							+ encodeURIComponent(c.calculation_breakdown || "")
							+ "&format=Product+Costing+Summary&no_letterhead=1";
						action_td = "<td style='padding:5px 8px;text-align:center;white-space:nowrap'>"
							+ "<a href='" + view_url + "' target='_blank' "
							+ "class='btn btn-xs btn-default' style='font-size:10.5px;margin-right:3px'>View</a>"
							+ "<a href='" + print_url + "' target='_blank' "
							+ "class='btn btn-xs btn-primary' style='font-size:10.5px' title='Download PDF'>PDF</a>"
							+ "</td>";
					} else {
						var draft_print_url = "/api/method/frappe.utils.print_format.download_pdf?doctype=Calculation+Breakdown&name="
							+ encodeURIComponent(c.calculation_breakdown || "")
							+ "&format=Product+Costing+Summary&no_letterhead=1";
						action_td = "<td style='padding:5px 8px;text-align:center;white-space:nowrap'>"
							+ "<button class='btn-edit-calc btn btn-xs btn-default' "
							+ "data-cb='" + c.calculation_breakdown + "' style='margin-right:4px' title='Open Calculator'>✏️</button>"
							+ "<a href='" + draft_print_url + "' target='_blank' "
							+ "class='btn btn-xs btn-default' style='margin-right:4px;font-size:10.5px' title='Download PDF'>🖨</a>"
							+ "<button class='btn-remove-calc btn btn-xs btn-danger' "
							+ "data-cb='" + c.calculation_breakdown + "' "
							+ "data-row='" + c.name + "' title='Remove'>🗑</button>"
							+ "</td>";
					}
					calc_rows +=
						"<tr style='border-bottom:1px solid #f0f0f0'>"
						+ "<td style='padding:5px 8px;font-size:11.5px;font-family:monospace'>"
						+ (c.calculation_breakdown || "") + "</td>"
						+ "<td style='padding:5px 8px;font-size:11.5px;color:#555'>"
						+ (c.description || "") + "</td>"
						+ "<td style='padding:5px 8px;text-align:right;font-family:monospace;font-weight:600'>"
						+ "LKR " + cur_fmt(c.unit_cost) + "</td>"
						+ action_td + "</tr>";
				});
			}

			// Build tax + margin rows (SSCL on cost → add margin → then VAT)
			var tax_rows = "";
			if (row.sscl) tax_rows += price_row("SSCL (" + _cc.sscl_rate + "%)", sscl_amt, "#fff8e1");
			if (margin_pct) tax_rows += price_row("Profit Margin (" + margin_pct + "%)", margin_amt, "#e8f5e9");
			if (row.vat) tax_rows += price_row("VAT (" + _cc.vat_rate + "%)", vat_amt, "#fff8e1");

			var add_btn = is_submitted ? "" :
				"<button class='btn-add-calc btn btn-xs btn-primary' "
				+ "data-item='" + item_name + "' data-cdt='" + cdt + "' data-cdn='" + cdn + "'>"
				+ "+ Add Calculation</button>";
			var copy_btn = is_submitted ? "" :
				"<button class='btn-copy-calc btn btn-xs btn-default' style='margin-left:6px' "
				+ "data-item='" + item_name + "' data-cdt='" + cdt + "' data-cdn='" + cdn + "' "
				+ "title='Copy an existing calculation and only change the quantity'>"
				+ "📋 Copy Qty</button>";
			var actions_th = is_submitted
				? "<th style='padding:5px 8px;color:#fff;font-size:10.5px;text-align:center'>Link</th>"
				: "<th style='padding:5px 8px;color:#fff;font-size:10.5px;text-align:center'>Actions</th>";

			// ── Cut sheet sizes strip (Offset only) ──
			var cut_strip = "";
			if ((ci.cut_sheet_l || 0) || (ci.cut_sheet_w || 0)) {
				var sz1 = (ci.cut_sheet_l || "—") + " × " + (ci.cut_sheet_w || "—") + " in";
				var sz2_part = "";
				if ((ci.cut_sheet_l_2 || 0) || (ci.cut_sheet_w_2 || 0)) {
					sz2_part = " &nbsp;·&nbsp; <b>Size 2:</b> "
						+ (ci.cut_sheet_l_2 || "—") + " × " + (ci.cut_sheet_w_2 || "—") + " in";
				}
				cut_strip = "<div style='margin-bottom:8px;padding:5px 8px;background:#eff6ff;"
					+ "border:1px solid #bfdbfe;border-radius:4px;font-size:11px;color:#1e3a5f'>"
					+ "✂ <b>Cut Sizes</b> — <b>Size 1:</b> " + sz1 + sz2_part + "</div>";
			}

			// ── Finishing operations strip ──
			var ops_html = "";
			var ops = frm.doc.operations || [];
			if (ops.length) {
				var badges = ops.map(function (op) {
					return "<span style='display:inline-block;margin:2px 3px 2px 0;padding:2px 8px;"
						+ "background:#dbeafe;border:1px solid #93c5fd;border-radius:12px;"
						+ "font-size:10.5px;color:#1e3a5f'>"
						+ frappe.utils.escape_html(op.operation || "") + "</span>";
				}).join("");
				ops_html = "<div style='margin-bottom:8px;padding:5px 8px;background:#f0fdf4;"
					+ "border:1px solid #bbf7d0;border-radius:4px;font-size:11px;color:#14532d'>"
					+ "<span style='font-weight:600'>Finishing:</span> " + badges + "</div>";
			}

			var html =
				"<div style='padding:12px 14px;background:#f8fafc;border-radius:5px;border:1px solid #e5e7eb'>"

				// ── Calculation list header ──
				+ "<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:8px'>"
				+ "<span style='font-size:12px;font-weight:700;color:#1a3a5c'>Calculations"
				+ (display_calcs.length ? " <span style='font-size:10px;background:#e0e7ff;color:#3730a3;"
					+ "border-radius:10px;padding:1px 7px;font-weight:600'>" + display_calcs.length + "</span>" : "")
				+ "</span>"
				+ "<span>" + add_btn + copy_btn + "</span>"
				+ "</div>"
				+ cut_strip
				+ ops_html

				// ── Calculation list table ──
				+ "<div style='border:1px solid #e5e7eb;border-radius:4px;overflow:hidden;margin-bottom:12px'>"
				+ "<table style='width:100%;border-collapse:collapse'>"
				+ "<thead><tr style='background:#1a3a5c'>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:10.5px;text-align:left'>Breakdown</th>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:10.5px;text-align:left'>Description</th>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:10.5px;text-align:right'>Unit Cost</th>"
				+ actions_th
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

				if (!is_submitted) {
					// ── Add Calculation button ──────────────────────
					$w.off("click.add_calc").on("click.add_calc", ".btn-add-calc", function () {
						show_add_calc_popup(frm, cdt, cdn, item_name);
					});

					// ── Copy Qty button (duplicate an existing calc at a new qty) ──
					$w.off("click.copy_calc").on("click.copy_calc", ".btn-copy-calc", function () {
						show_copy_calc_popup(frm, cdt, cdn, item_name);
					});

					// ── Edit (open calculator) button ───────────────
					$w.off("click.edit_calc").on("click.edit_calc", ".btn-edit-calc", function () {
						var cb = $(this).data("cb");
						if (cb) {
							var pt = frm.doc.pricing_type || "Offset";
							var url = "/app/offset-calculator?ref=" + encodeURIComponent(cb)
								+ "&cost_sheet=" + encodeURIComponent(frm.doc.name)
								+ "&pricing_type=" + encodeURIComponent(pt);
							window.location.href = url;
						}
					});

					// ── Remove calculation button ───────────────────
					$w.off("click.remove_calc").on("click.remove_calc", ".btn-remove-calc", function () {
						var cb = $(this).data("cb");
						frappe.confirm(
							"Remove calculation <b>" + cb + "</b> from this item?",
							function () {
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
				}

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

	// If this item belongs to a group, use the group's total qty for the calculation
	// (e.g. Mango×500 + Strawberry×1000 + Mixed×1000 → calculate at 2500)
	var cs_row_for_item = (frm.doc.pricing_list || []).find(function (r) { return r.item === item_name; });
	var group_name_val = cs_row_for_item ? (cs_row_for_item.group_name || "").trim() : "";
	var group_total_qty = 0;
	if (group_name_val) {
		(frm.doc.pricing_list || []).forEach(function (r) {
			if ((r.group_name || "").trim() === group_name_val) group_total_qty += flt_v(r.qty);
		});
	}

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
					fieldtype: "Select", fieldname: "material_type",
					label: "Material Type",
					options: "Existing\nCustom",
					default: "Existing",
					description: "Existing = select from Item master. Custom = enter name manually (non-stock/unregistered board).",
				},
				{
					fieldtype: "Link", fieldname: "base_material",
					label: "Base Material (Item)", options: "Item",
					depends_on: "eval:doc.material_type !== 'Custom'",
					mandatory_depends_on: "eval:doc.material_type !== 'Custom'",
				},
				{
					fieldtype: "Data", fieldname: "custom_material_name",
					label: "Material Name",
					depends_on: "eval:doc.material_type === 'Custom'",
					mandatory_depends_on: "eval:doc.material_type === 'Custom'",
					description: "e.g. 300gsm Chrome Board (unregistered) — will appear on cost breakdown",
				},
				{
					fieldtype: "Currency", fieldname: "material_rate",
					label: "Material Rate (LKR / full sheet)",
					depends_on: "eval:doc.material_type === 'Custom'",
					mandatory_depends_on: "eval:doc.material_type === 'Custom'",
					description: "Rate per full sheet for this custom material.",
				},
			];

			if (pricingType === "Flexo") {
				// Flexo: reel dimensions (stored only in ui_state, no dedicated CB fields)
				fields = fields.concat([
					{ fieldtype: "Section Break", label: "Reel Dimensions (mm)" },
					{ fieldtype: "Float", fieldname: "reel_width_mm", label: "Reel Width (mm)", reqd: 1 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Section Break", label: "Product Dimensions (mm)" },
					{ fieldtype: "Float", fieldname: "product_width_mm", label: "Product Width (mm)", reqd: 1 },
					{ fieldtype: "Float", fieldname: "product_margin_mm", label: "Margin (mm)", default: 4 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Float", fieldname: "product_length_mm", label: "Product Length (mm)", reqd: 1 },
					{ fieldtype: "Float", fieldname: "product_gap_mm", label: "Gap (mm)", default: 3 },
					{ fieldtype: "Section Break" },
					{ fieldtype: "Int", fieldname: "no_of_colors", label: "No of Colors", default: ci.colour || 0 },
					{ fieldtype: "Column Break" },
				]);
			} else {
				// Offset: sheet dimensions (saved to CB doctype fields)
				fields = fields.concat([
					{ fieldtype: "Section Break", label: "Full Sheet (Inches)" },
					{ fieldtype: "Float", fieldname: "full_sheet_l", label: "Full Sheet Length (L)", reqd: 1 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Float", fieldname: "full_sheet_w", label: "Full Sheet Width (W)", reqd: 1 },
					{ fieldtype: "Section Break", label: "Cut Sheet 1 (Inches)" },
					{ fieldtype: "Float", fieldname: "cut_sheet_l", label: "Cut Sheet 1 — L", reqd: 1, default: ci.cut_sheet_l || 0 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Float", fieldname: "cut_sheet_w", label: "Cut Sheet 1 — W", reqd: 1, default: ci.cut_sheet_w || 0 },
					{ fieldtype: "Section Break", label: "Cut Sheet 2 (Optional, Inches)" },
					{ fieldtype: "Float", fieldname: "cut_sheet_l_2", label: "Cut Sheet 2 — L", default: ci.cut_sheet_l_2 || 0, description: "Leave 0 if only one cut size." },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Float", fieldname: "cut_sheet_w_2", label: "Cut Sheet 2 — W", default: ci.cut_sheet_w_2 || 0 },
					{ fieldtype: "Section Break", label: "Cuts & Ups" },
					{ fieldtype: "Int", fieldname: "no_of_cuts", label: "No of Cuts", default: 2, reqd: 1 },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Int", fieldname: "no_of_ups", label: "No of Ups", default: 4, reqd: 1 },
					{ fieldtype: "Section Break", label: "Colors" },
					{ fieldtype: "Int", fieldname: "no_of_colors", label: "No of Colors", default: ci.colour || 0 },
				]);
			}

			// Qty + description — common to both
			var default_qty = group_total_qty || ci.item_qty || 0;
			var qty_desc = group_name_val
				? "Group total: sum of all items in \"" + group_name_val + "\""
				: "Auto-filled from Cost Item";
			fields = fields.concat([
				{ fieldtype: "Section Break", label: "Quantity" },
				{
					fieldtype: "Float", fieldname: "item_qty",
					label: "Item Qty", default: default_qty,
					description: qty_desc,
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
					create_calc_breakdown_and_open(frm, cdt, cdn, item_name, ci, vals, pricingType, group_name_val);
				},
			});

			d.show();
		},
	});
}


// ─────────────────────────────────────────────────────────────
//  COPY CALCULATION — reuse an existing calc, change only the qty
//  No re-entering specs. Server recalculates at the new qty and
//  saves a new Calculation Breakdown linked to this item.
// ─────────────────────────────────────────────────────────────

function show_copy_calc_popup(frm, cdt, cdn, item_name) {
	var row = locals[cdt][cdn];
	var target_qty = flt_v(row.qty) || 0;

	frappe.call({
		method: "nxtgen_savinda_pricing_calculator.api.offset_calculator.get_cost_sheet_calculations",
		args: { cost_sheet: frm.doc.name },
		callback: function (r) {
			var calcs = (r.message || []).filter(function (c) { return c.cb; });
			if (!calcs.length) {
				frappe.msgprint({
					title: "Nothing to copy",
					message: "There are no existing calculations in this Cost Sheet yet. "
						+ "Create one with <b>+ Add Calculation</b> first, then you can copy it at a different qty.",
					indicator: "orange",
				});
				return;
			}

			// Build label→cb map (Select shows descriptive labels)
			var label_to_cb = {};
			var labels = calcs.map(function (c) {
				var lbl = (c.source_item || c.cb)
					+ "  |  qty " + (c.item_qty || 0).toLocaleString()
					+ "  |  LKR " + cur_fmt(c.unit_cost)
					+ "  |  " + c.cb;
				label_to_cb[lbl] = c.cb;
				return lbl;
			});

			var d = new frappe.ui.Dialog({
				title: "Copy Calculation — change qty only",
				fields: [
					{
						fieldtype: "HTML",
						options: "<div style='padding:7px 10px;background:#f0f4ff;border-radius:4px;"
							+ "font-size:12px;color:#1a3a5c;margin-bottom:6px'>"
							+ "Copies an existing calculation's full setup (material, machine, specs) and "
							+ "<b>only changes the quantity</b>. The cost is recalculated automatically — "
							+ "you don't re-enter anything.</div>",
					},
					{
						fieldtype: "Select", fieldname: "source_label",
						label: "Source Calculation",
						options: labels.join("\n"),
						default: labels[0], reqd: 1,
					},
					{
						fieldtype: "Float", fieldname: "new_qty",
						label: "New Order Qty", default: target_qty, reqd: 1,
						description: "Defaults to this item's qty (" + target_qty.toLocaleString() + ").",
					},
					{
						fieldtype: "Data", fieldname: "description",
						label: "Description (optional)",
						description: "e.g. 5,000 qty variant",
					},
				],
				primary_action_label: "Create Copy",
				primary_action: function (vals) {
					var source_cb = label_to_cb[vals.source_label];
					if (!source_cb) {
						frappe.msgprint({ message: "Please select a source calculation.", indicator: "orange" });
						return;
					}
					d.hide();
					frappe.call({
						method: "nxtgen_savinda_pricing_calculator.api.offset_calculator.copy_calculation",
						args: {
							source_cb: source_cb,
							target_cost_item: item_name,
							new_qty: vals.new_qty,
							description: vals.description || "",
						},
						freeze: true,
						freeze_message: "Copying calculation…",
						callback: function (res) {
							if (res.message && res.message.new_cb) {
								frappe.show_alert({
									message: "Copied → " + res.message.new_cb
										+ " (qty " + (res.message.item_qty || 0).toLocaleString() + ")",
									indicator: "green",
								});
								render_panel(frm, cdt, cdn);
								refresh_row_prices(frm, cdt, cdn);
							} else {
								frappe.msgprint({
									title: "Copy failed",
									message: (res.message && res.message.error) || "Unknown error.",
									indicator: "red",
								});
							}
						},
					});
				},
			});
			d.show();
		},
	});
}


// ─────────────────────────────────────────────────────────────
//  CREATE Calculation Breakdown → link to Cost Item → open calculator
// ─────────────────────────────────────────────────────────────

function create_calc_breakdown_and_open(frm, cdt, cdn, item_name, ci, vals, pricingType, group_name) {

	frappe.show_alert({ message: "Creating Calculation Breakdown…", indicator: "blue" });

	// Collect peer Cost Items in the same group (excluding the primary item)
	var peer_items = [];
	if (group_name) {
		(frm.doc.pricing_list || []).forEach(function (r) {
			if ((r.group_name || "").trim() === group_name.trim() && r.item && r.item !== item_name) {
				peer_items.push(r.item);
			}
		});
	}

	var matType = (vals.material_type || "Existing");
	var cbDoc = {
		doctype: "Calculation Breakdown",
		customer_name: frm.doc.customer_name || "",
		ref: frm.doc.inquiry || "",
		pricing_type: pricingType || "Offset",
		material_type: matType,
		base_material: matType === "Custom" ? "" : (vals.base_material || ""),
		custom_material_name: matType === "Custom" ? (vals.custom_material_name || "") : "",
		material_rate: matType === "Custom" ? (parseFloat(vals.material_rate) || 0) : 0,
		item_qty: vals.item_qty,
		no_of_colors: vals.no_of_colors,
	};

	if (pricingType !== "Flexo") {
		cbDoc.full_sheet_l = vals.full_sheet_l;
		cbDoc.full_sheet_w = vals.full_sheet_w;
		cbDoc.cut_sheet_l = vals.cut_sheet_l;
		cbDoc.cut_sheetw = vals.cut_sheet_w;
		cbDoc.no_of_cuts = vals.no_of_cuts;
		cbDoc.no_of_ups = vals.no_of_ups;
	}

	// Seed ui_state so the calculator restores EVERY value entered in this dialog.
	// Flexo reel dimensions have no dedicated CB columns — they live only in ui_state —
	// so without this they were lost when the calculator opened.
	var cbForm = {
		pricing_type:  pricingType || "Offset",
		customer_name: frm.doc.customer_name || "",
		ref:           frm.doc.inquiry || "",
		material_type: matType,
		base_material: matType === "Custom" ? "" : (vals.base_material || ""),
		custom_material_name: matType === "Custom" ? (vals.custom_material_name || "") : "",
		material_rate: matType === "Custom" ? (parseFloat(vals.material_rate) || 0) : 0,
		no_of_colors:  parseInt(vals.no_of_colors) || 0,
		item_qty:      parseFloat(vals.item_qty) || 0,
	};
	if (pricingType === "Flexo") {
		cbForm.reel_width_mm     = parseFloat(vals.reel_width_mm) || 0;
		cbForm.product_width_mm  = parseFloat(vals.product_width_mm) || 0;
		cbForm.product_length_mm = parseFloat(vals.product_length_mm) || 0;
		cbForm.product_margin_mm = parseFloat(vals.product_margin_mm) || 0;
		cbForm.product_gap_mm    = parseFloat(vals.product_gap_mm) || 0;
	} else {
		cbForm.full_sheet_l = parseFloat(vals.full_sheet_l) || 0;
		cbForm.full_sheet_w = parseFloat(vals.full_sheet_w) || 0;
		cbForm.cut_sheet_l  = parseFloat(vals.cut_sheet_l) || 0;
		cbForm.cut_sheet_w  = parseFloat(vals.cut_sheet_w) || 0;
		cbForm.no_of_cuts   = parseInt(vals.no_of_cuts) || 0;
		cbForm.no_of_ups    = parseInt(vals.no_of_ups) || 0;
	}
	cbDoc.ui_state = JSON.stringify({ form: cbForm, selected_specs: [], machine_spec: null, calc_result: null });

	// 1. Create the Calculation Breakdown
	frappe.call({
		method: "frappe.client.insert",
		args: { doc: cbDoc },
		callback: function (r) {
			if (!r.message) return;
			var cb_name = r.message.name;

			// 2. Add CB to primary Cost Item's calculations table
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
					// Save cut sheet sizes to Cost Item (Offset only)
					if (pricingType !== "Flexo") {
						doc.cut_sheet_l = parseFloat(vals.cut_sheet_l) || 0;
						doc.cut_sheet_w = parseFloat(vals.cut_sheet_w) || 0;
						doc.cut_sheet_l_2 = parseFloat(vals.cut_sheet_l_2) || 0;
						doc.cut_sheet_w_2 = parseFloat(vals.cut_sheet_w_2) || 0;
					}

					frappe.call({
						method: "frappe.client.save",
						args: { doc: doc },
						callback: function () {
							frappe.show_alert({ message: "Created: " + cb_name, indicator: "green" });

							// 3. Link CB to all peer Cost Items in the same group, then open calculator
							function link_peer(idx) {
								if (idx >= peer_items.length) {
									// Build bqtys param — controls per-breakdown SHEET SPLIT (wastage per qty).
									//  1. Combine-mode item (breakdown_qtys_json) → split by those qtys
									//  2. Grouped items (group_name set) → split by the group's member qtys (selected group list)
									//  3. No group, single item → NO sheet split (one item_qty, single wastage)
									var bqtys_param = "";
									var bqtys = [];
									try {
										var json_bqtys = ci.breakdown_qtys_json ? JSON.parse(ci.breakdown_qtys_json) : [];
										if (json_bqtys && json_bqtys.length > 1) {
											bqtys = json_bqtys;
										}
									} catch (e) { }
									if (!bqtys.length && group_name) {
										// Group set → sheet split across all member quantities in this group
										(frm.doc.pricing_list || []).forEach(function (r) {
											if (r.item && (r.group_name || "").trim() === group_name.trim()) {
												bqtys.push(flt_v(r.qty));
											}
										});
									}
									bqtys = bqtys.filter(function (q) { return q > 0; });
									if (bqtys.length > 1) {
										bqtys_param = "&bqtys=" + encodeURIComponent(bqtys.join(","));
									}
									var url = "/app/offset-calculator?ref=" + encodeURIComponent(cb_name)
										+ "&cost_sheet=" + encodeURIComponent(frm.doc.name)
										+ "&pricing_type=" + encodeURIComponent(pricingType || "Offset")
										+ bqtys_param
										+ get_operations_param(frm);
									window.location.href = url;
									return;
								}
								frappe.call({
									method: "frappe.client.get",
									args: { doctype: "cost Item", name: peer_items[idx] },
									callback: function (rp) {
										if (rp.message) {
											var pdoc = rp.message;
											if (!pdoc.calculations) pdoc.calculations = [];
											var already = pdoc.calculations.some(function (c) {
												return c.calculation_breakdown === cb_name;
											});
											if (!already) {
												pdoc.calculations.push({
													doctype: "Cost Item Calculation",
													calculation_breakdown: cb_name,
													description: vals.description || "",
													unit_cost: 0,
													amount: 0,
												});
												frappe.call({
													method: "frappe.client.save",
													args: { doc: pdoc },
													callback: function () { link_peer(idx + 1); },
													error: function () { link_peer(idx + 1); },
												});
												return;
											}
										}
										link_peer(idx + 1);
									},
									error: function () { link_peer(idx + 1); },
								});
							}
							link_peer(0);
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
	var margin_pct_r = flt_v(row.profit_margin);
	var sscl_amt_r = row.sscl ? round2(unit * (_cc.sscl_rate / 100)) : 0;
	var cost_with_sscl_r = unit + sscl_amt_r;
	var margin_amt_r = margin_pct_r ? round2(cost_with_sscl_r * (margin_pct_r / 100)) : 0;
	var qu_r = round2(cost_with_sscl_r + margin_amt_r);
	var vat_amt_r = row.vat ? round2(qu_r * (_cc.vat_rate / 100)) : 0;
	var sell_unit = round2(qu_r + vat_amt_r);

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
		fieldtype: "Data", fieldname: "group_name",
		label: "Group Name (optional)",
		description: "If set, all created items share this group — the quotation will show one combined row.",
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
			do_create_items(frm, opp, subject, pages, breakdowns, selected, total_bd_qty, values.group_name || "");
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
	// If no breakdowns (or only a blank placeholder row), create one item directly
	// using the Inquiry's Item Qty field (custom_item_qty) as the quantity.
	if (!breakdowns.length || (breakdowns.length === 1 && !breakdowns[0].description && !breakdowns[0].qty)) {
		var qty = flt_v(opp.custom_item_qty) || (breakdowns.length > 0 ? flt_v(breakdowns[0].qty) : 0);
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
			+ "<b>Individual mode:</b> each selected breakdown → separate Cost Item.<br>"
			+ "<b>Combined mode:</b> all selected breakdowns → ONE Cost Item (sheet requirements calculated per-breakdown, summed).</div>",
	}, {
		fieldtype: "Check", fieldname: "combine_mode",
		label: "Combine selected breakdowns into ONE calculation item",
		default: 0,
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
		fieldtype: "Data", fieldname: "group_name",
		label: "Group Name (optional)",
		description: "If set, all created items share this group — the quotation will show one combined row.",
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
			var group_name_val = values.group_name || "";
			var selected_bds = breakdowns.filter(function (bd, i) {
				return values["sel_bd_" + i];
			});
			if (!selected_bds.length) {
				frappe.msgprint({ message: "No breakdowns selected.", indicator: "orange" }); return;
			}

			if (values.combine_mode && selected_bds.length > 1) {
				// ── COMBINED MODE: create ONE Cost Item with total qty + breakdown_qtys_json ──
				var total_qty = selected_bds.reduce(function (s, b) { return s + flt_v(b.qty); }, 0);
				var breakdown_qtys = selected_bds.map(function (b) { return flt_v(b.qty); });
				var descriptions = selected_bds.map(function (b) { return (b.description || "").trim(); }).filter(Boolean);
				var combined_desc = descriptions.join(" + ");
				var name = combined_desc ? subject + " - " + combined_desc : subject;
				var comb_pk = _cs_packing(selected_bds[0]);
				var comb_doc = {
					doctype: "cost Item", cost_item_name: name,
					inquiry: opp.name, subject: subject, page_type: "",
					colour: cint_v(opp.custom_colour), material: "",
					item_qty: total_qty, no_of_pages: 0,
					breakdown: combined_desc, is_selected: 1,
					breakdown_qtys_json: JSON.stringify(breakdown_qtys),
				};
				_apply_packing(comb_doc, comb_pk);
				frappe.call({
					method: "frappe.client.insert",
					args: { doc: comb_doc },
					callback: function (r) {
						if (r.message) {
							var row = frm.add_child("pricing_list");
							row.item = r.message.name;
							row.item_name = name;
							row.qty = total_qty;
							if (group_name_val) row.group_name = group_name_val;
							_apply_packing(row, comb_pk);
						}
						frm.refresh_field("pricing_list");
						frm.save();
						frappe.show_alert({
							message: "Combined cost item created (" + breakdown_qtys.length + " breakdowns, total qty " + total_qty.toLocaleString() + ")",
							indicator: "green",
						});
					},
				});
				return;
			}

			// ── INDIVIDUAL MODE: one Cost Item per selected breakdown ──
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
				var bd_pk = _cs_packing(bd);
				var bd_doc = {
					doctype: "cost Item", cost_item_name: name,
					inquiry: opp.name, subject: subject, page_type: "",
					colour: cint_v(opp.custom_colour), material: "",
					item_qty: qty, no_of_pages: 0,
					breakdown: bd_desc, is_selected: 1,
				};
				_apply_packing(bd_doc, bd_pk);
				frappe.call({
					method: "frappe.client.insert",
					args: { doc: bd_doc },
					callback: function (r) {
						if (r.message) {
							var row = frm.add_child("pricing_list");
							row.item = r.message.name;
							row.item_name = name;
							row.qty = qty;
							if (group_name_val) row.group_name = group_name_val;
							_apply_packing(row, bd_pk);
						}
						insert_bd_next(idx + 1);
					},
					error: function () { insert_bd_next(idx + 1); },
				});
			}
			insert_bd_next(0);
		},
	});

	// Live preview updates when checkboxes change
	function update_preview() {
		var combine = d.get_value("combine_mode");
		var sel = breakdowns.filter(function (bd, i) { return d.get_value("sel_bd_" + i); });
		var rows;
		if (combine && sel.length > 1) {
			var total = sel.reduce(function (s, b) { return s + flt_v(b.qty); }, 0);
			var combined_name = sel.map(function (b) { return (b.description || "").trim(); }).filter(Boolean).join(" + ");
			rows = "<tr style='background:#e8f5e9'>"
				+ "<td style='padding:5px 8px;font-weight:600;font-size:12px'>" + subject + (combined_name ? " - " + combined_name : "") + "</td>"
				+ "<td style='padding:5px 8px;text-align:right;font-family:monospace;color:#2e7d32;font-weight:700'>" + total.toLocaleString() + "</td>"
				+ "<td style='padding:5px 8px;font-size:10px;color:#555'>"
				+ sel.map(function (b) { return flt_v(b.qty).toLocaleString(); }).join(" + ") + "</td></tr>";
			rows = "<thead><tr style='background:#1a3a5c'>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:11px;text-align:left'>Cost Item</th>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:11px;text-align:right'>Total Qty</th>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:11px'>Breakdown Qtys</th>"
				+ "</tr></thead><tbody>" + rows + "</tbody>";
		} else {
			rows = sel.map(function (bd) {
				var bd_desc = (bd.description || "").trim();
				return "<tr><td style='padding:4px 8px;font-weight:600;font-size:12px'>"
					+ (bd_desc ? subject + " - " + bd_desc : subject) + "</td>"
					+ "<td style='padding:4px 8px;text-align:right;font-family:monospace'>"
					+ flt_v(bd.qty).toLocaleString() + "</td></tr>";
			}).join("");
			rows = "<thead><tr style='background:#1a3a5c'>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:11px;text-align:left'>Cost Item Name</th>"
				+ "<th style='padding:5px 8px;color:#fff;font-size:11px;text-align:right'>Qty</th>"
				+ "</tr></thead><tbody>" + rows + "</tbody>";
		}
		d.$wrapper.find("#cs-single-preview").html(
			sel.length ? "<table style='width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-radius:4px'>"
				+ rows + "</table>" : ""
		);
	}

	d.$wrapper.on("change", "input[type=checkbox]", update_preview);
	d.show();
	setTimeout(update_preview, 100);
}

// Packing Info seeded ONCE from an inquiry breakdown row. Kept editable downstream;
// the final saved value on the Cost Item is what flows forward (inquiry not re-read).
function _cs_packing(bd) {
	return {
		packing_type:      (bd && bd.packing_type) || "",
		winding_direction: (bd && bd.winding_direction) || "",
		pcs_per_role:      (bd && bd.pcs_per_role) || 0,
		up:                (bd && bd.up) || 0,
		is_printed:        (bd && bd.is_printed) || "",
	};
}
function _apply_packing(target, pk) {
	if (!target || !pk) return;
	target.packing_type      = pk.packing_type;
	target.winding_direction = pk.winding_direction;
	target.pcs_per_role      = pk.pcs_per_role;
	target.up                = pk.up;
	target.is_printed        = pk.is_printed;
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
					_packing: _cs_packing(bd),
					_calc: "bd_qty(" + flt_v(bd.qty).toLocaleString() + ") × pages(" + n_pages + ")"
				});
			});
		} else {
			// Grouped-per-page item spans all breakdowns → seed packing from the first row.
			items.push({
				cost_item_name: subject + " - " + page_type + " - " + colour + "C",
				page_type: page_type, colour: colour, material: material,
				item_qty: n_pages * total_bd_qty, no_of_pages: n_pages,
				breakdown: "", bd_qty: total_bd_qty, is_selected: 0,
				_packing: _cs_packing(breakdowns[0]),
				_calc: "pages(" + n_pages + ") × total_bd(" + total_bd_qty.toLocaleString() + ")"
			});
		}
	});
	return items;
}

function do_create_items(frm, opp, subject, pages, breakdowns, selected, total_bd_qty, group_name) {
	var items = build_items(subject, pages, breakdowns, selected, total_bd_qty);
	if (!items.length) { frappe.msgprint({ message: "No items.", indicator: "orange" }); return; }
	frappe.show_alert({ message: "Creating " + items.length + " cost item(s)…", indicator: "blue" });

	function insert_next(idx) {
		if (idx >= items.length) {
			frm.refresh_field("pricing_list");
			frm.save();
			frappe.show_alert({ message: items.length + " cost item(s) created.", indicator: "green" });
			return;
		}
		var ci = items[idx];
		var ci_doc = {
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
		};
		_apply_packing(ci_doc, ci._packing);
		frappe.call({
			method: "frappe.client.insert",
			args: { doc: ci_doc },
			callback: function (r) {
				if (r.message) {
					var row = frm.add_child("pricing_list");
					row.item = r.message.name;
					row.item_name = ci.cost_item_name;
					row.qty = ci.item_qty;
					if (group_name) row.group_name = group_name;
					_apply_packing(row, ci._packing);
				}
				insert_next(idx + 1);
			},
			error: function () {
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