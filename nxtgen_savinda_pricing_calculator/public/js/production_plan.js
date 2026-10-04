// Copyright (c) 2026, Techincglobal.com
// Production Plan workflow, Manufacturing Planning, and material-request actions.
// Base-material quantities come from Manufacturing Planning sheet/reel calculations.

frappe.ui.form.on("Production Plan", {
	refresh: function (frm) {
		// Custom actions follow the Production Plan workflow. This keeps a user from
		// performing a later team's task while the plan is still with another team.
		var is_ticket_plan = !frm.is_new() && !!frm.doc.custom_ticket_type;
		var is_draft = frm.doc.docstatus === 0;
		var workflow_state = frm.doc.workflow_state || "Draft";
		var is_cs_stage = is_ticket_plan && is_draft && workflow_state === "Draft";
		var is_bom_stage = is_ticket_plan && is_draft && workflow_state === "BOM Validation";
		var is_system_manager = frappe.user.has_role("System Manager");
		var is_supply_stage = is_ticket_plan && is_draft && workflow_state === "Supply Chain Validation";
		var can_view_ticket = is_ticket_plan && [
			"Supply Chain Validation", "Approved", "Submitted"
		].indexOf(workflow_state) !== -1;

		// Base-material wastage is calculated from Manufacturing Planning when raw
		// materials are fetched. Do not offer the former percentage Add Wastage action.

		// CS Team: prepare and verify the source details before handing off to BOM.
		if (is_cs_stage && (frm.doc.po_items || []).length) {
			frm.add_custom_button(__("Fetch Job Ticket Details"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.fetch_ticket_details",
					args: { production_plan: frm.doc.name },
					freeze: true, freeze_message: __("Fetching…"),
					callback: function () {
						frappe.show_alert({ message: __("Job Ticket details fetched."), indicator: "green" });
						frm.reload_doc();
					},
				});
			}, __("Actions"));
		}

		// BOM Team: build/reconcile BOMs only during BOM Validation.
		if (is_bom_stage) {
			if (frm.doc.custom_needs_bom) {
				frm.dashboard.set_headline(
					'<span class="indicator orange">Some items have no BOM</span> — Open BOM Builder to create them, then Sync BOMs.'
				);
			}
			frm.add_custom_button(__("Open BOM Builder"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.bom_builder_url",
					args: { production_plan: frm.doc.name },
					callback: function (r) {
						if (r.message) { window.open(r.message); }
						else { frappe.msgprint(__("No source (Sales Order / Cost Sheet) available to open the BOM Builder.")); }
					},
				});
			}, __("Actions"));

			frm.add_custom_button(__("Sync BOMs"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.sync_boms",
					args: { production_plan: frm.doc.name },
					freeze: true, freeze_message: __("Syncing BOMs…"),
					callback: function (r) {
						var m = r.message || {};
						frappe.msgprint({
							title: __("Sync BOMs"),
							message: __("Resolved: {0}<br>Still without BOM: {1}",
								[(m.resolved || []).join(", ") || "—", (m.still_missing || []).join(", ") || "—"]),
							indicator: (m.still_missing || []).length ? "orange" : "green",
						});
						frm.reload_doc();
					},
				});
			}, __("Actions"));
		}

		// CS Team defines runs in Draft. A System Manager can recover a plan that was
		// sent to BOM Validation before the runs were added, without exposing the
		// planning action to ordinary BOM users.
		if (is_draft && is_ticket_plan) {
			frm.add_custom_button(__("Refresh Planning Calculations"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.refresh_planning_calculations",
					args: { production_plan: frm.doc.name }, freeze: true, freeze_message: __("Refreshing planning data…"),
					callback: function (r) {
						frappe.show_alert({ message: __("Refreshed {0} planning row(s).", [(r.message || {}).rows || 0]), indicator: "green" });
						frm.reload_doc();
					},
				});
			}, __("Actions"));
		}

		if (is_cs_stage || (is_bom_stage && is_system_manager)) {
			frm.add_custom_button(__("Break Down Job Ticket Item"), function () {
				_show_job_ticket_breakdown_dialog(frm);
			}, __("Actions"));
			frm.add_custom_button(__("Get Finished Goods for Manufacture"), function () {
				_get_manufacture_fg_dialog(frm);
			}, __("Actions"));
			frm.add_custom_button(__("Get BOM Items for Manufacture"), function () {
				frappe.confirm(__("This replaces current Manufacturing Planning rows with BOM sub-assemblies and final assembly rows. Continue?"), function () {
					frappe.call({
						method: "nxtgen_savinda_pricing_calculator.api.production_plan.add_bom_items_for_manufacture",
						args: { production_plan: frm.doc.name }, freeze: true, freeze_message: __("Expanding BOMs…"),
						callback: function (r) {
							var m = r.message || {};
							frappe.show_alert({ message: __("Added {0} planning rows, including {1} assembly row(s).", [m.added || 0, m.assembly_rows || 0]), indicator: "green" });
							frm.reload_doc();
						},
					});
				});
			}, __("Actions"));
		}

		// Supply Chain: create purchasing demand only in Stock/Supply validation.
		if (is_supply_stage
			&& (frm.doc.mr_items || []).length) {
			frm.add_custom_button(__("Create Purchase Request"), function () {
				frappe.call({
					method: "nxtgen_savinda_pricing_calculator.api.production_plan.create_purchase_request",
					args: { production_plan: frm.doc.name },
					freeze: true, freeze_message: __("Creating Purchase Request…"),
					callback: function (r) {
						var m = r.message || {};
						if (m.material_request) {
							frappe.msgprint({
								title: __("Purchase Request"),
								message: __("Created: <a href='/app/material-request/{0}' target='_blank'>{0}</a>", [m.material_request]),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			}, __("Actions"));
		}

		// Re-request is available only after the initial ERPNext Material Request was
		// submitted. Assembly rows are deliberately excluded by the API.
		if (is_ticket_plan && frm.doc.docstatus === 1) {
			frappe.call({
				method: "nxtgen_savinda_pricing_calculator.api.production_plan.can_re_request_base_material",
				args: { production_plan: frm.doc.name },
				callback: function (r) {
					if (r.message) {
						frm.add_custom_button(__("Re-Request Base Material"), function () {
							_show_base_material_request_dialog(frm);
						}, __("Create"));
					}
				},
			});
		}

		// The approved production document is relevant from Artwork Approval onward.
		if (can_view_ticket) {
			frm.add_custom_button(__("View/Download Job Ticket"), function () {
				var fmt = (frm.doc.custom_pricing_type === "Flexo") ? "Flexo Job Ticket" : "Offset Job Ticket";
				var url = "/api/method/frappe.utils.print_format.download_pdf?doctype=Production+Plan&name="
					+ encodeURIComponent(frm.doc.name)
					+ "&format=" + encodeURIComponent(fmt) + "&no_letterhead=1";
				window.open(frappe.urllib.get_full_url(url));
			});
		}


	},
	custom_ticket_type: function (frm, cdt, cdn) {
		// alert("Ticket Type changed — clearing planning rows and resetting source document.");
		if (frm.doc.custom_ticket_type == "Job") {
			frm.set_value('get_items_from', 'Sales Order');
			// frm.doc.get_items_from = "Sale Order";

		} else if (frm.doc.custom_ticket_type == "NPD") {
			frm.set_value('get_items_from', 'Material Request');
		}
	}
});

function _show_job_ticket_breakdown_dialog(frm) {
	var rows = (frm.doc.custom_ticket_items || []).filter(function (r) { return (parseFloat(r.qty) || 0) > 1; });
	if (!rows.length) { frappe.msgprint(__("There is no Job Ticket item with a quantity available to split.")); return; }
	var labels = rows.map(function (r) { return r.name + " — " + (r.description || r.fg_item || "Item") + " (Qty: " + r.qty + ")"; });
	var by_name = {}; rows.forEach(function (r) { by_name[r.name] = r; });
	var d = new frappe.ui.Dialog({
		title: __("Break Down Job Ticket Item"),
		fields: [
			{ fieldtype: "Select", fieldname: "ticket_item", label: "Job Ticket Item", options: labels.join("\n"), reqd: 1 },
			{
				fieldtype: "Float", fieldname: "first_qty", label: "First Line Qty", reqd: 1,
				description: "The remaining quantity is automatically added as a new line with the same item details."
			},
		],
		primary_action_label: __("Split into Two Lines"),
		primary_action: function (v) {
			var name = (v.ticket_item || "").split(" — ")[0];
			var row = by_name[name];
			if (!row) return;
			var q = parseFloat(v.first_qty) || 0;
			if (q <= 0 || q >= (parseFloat(row.qty) || 0)) {
				frappe.msgprint(__("First Line Qty must be between 0 and {0}.", [row.qty])); return;
			}
			frappe.call({
				method: "nxtgen_savinda_pricing_calculator.api.production_plan.split_job_ticket_item",
				args: { production_plan: frm.doc.name, ticket_item: name, first_qty: q },
				freeze: true, freeze_message: __("Splitting item…"),
				callback: function (r) { d.hide(); frappe.show_alert({ message: __("Created the second Job Ticket line with Qty {0}.", [(r.message || {}).second_qty]), indicator: "green" }); frm.reload_doc(); },
			});
		},
	});
	d.show();
}

// "Get Finished Goods for Manufacture" — list FGs with editable qty, then populate the
// planning table (print data computed from each item's cost calculation).
function _get_manufacture_fg_dialog(frm) {
	frappe.call({
		method: "nxtgen_savinda_pricing_calculator.api.production_plan.get_manufacture_fg_list",
		args: { production_plan: frm.doc.name }, freeze: true,
		callback: function (r) {
			var items = r.message || [];
			if (!items.length) { frappe.msgprint(__("No finished goods found on this plan.")); return; }
			var is_offset = (frm.doc.custom_pricing_type || "Offset") !== "Flexo";
			var rows = items.map(function (it, i) {
				return "<tr>"
					+ "<td style='text-align:center'><input type='checkbox' class='fg-sel' data-idx='" + i + "' checked></td>"
					+ "<td>" + frappe.utils.escape_html(it.item_name || it.fg_item || "") + "</td>"
					+ "<td><input type='number' min='0' class='fg-qty' data-idx='" + i + "' value='" + (it.qty || 0) + "' style='width:110px'></td>"
					+ "</tr>";
			}).join("");
			var html = "<table class='table table-bordered' style='font-size:12px;margin-bottom:0'>"
				+ "<thead><tr><th style='width:40px'></th><th>Item</th><th style='width:120px'>Order Qty</th></tr></thead>"
				+ "<tbody>" + rows + "</tbody></table>";
			var fields = [];
			if (is_offset) {
				fields.push({
					fieldtype: "Check", fieldname: "consolidate", label: __("Consolidate Sales Order Items"),
					description: __("Treat the selection as one combined print run — full/cut sheet qty & wastage on the first row only; other rows show only ups & cuts.")
				});
			}
			fields.push({ fieldtype: "HTML", fieldname: "tbl", options: html });
			var d = new frappe.ui.Dialog({
				title: __("Get Finished Goods for Manufacture"), size: "large", fields: fields,
				primary_action_label: __("Add to Planning"),
				primary_action: function (v) {
					var $w = d.fields_dict.tbl.$wrapper;
					var sel = [];
					$w.find(".fg-sel:checked").each(function () {
						var idx = parseInt($(this).attr("data-idx"), 10);
						var qty = parseFloat($w.find(".fg-qty[data-idx='" + idx + "']").val()) || 0;
						if (qty <= 0) { frappe.msgprint(__("Order Qty must be greater than zero.")); return false; }
						var it = items[idx];
						sel.push({
							fg_item: it.fg_item, item_name: it.item_name, cost_item: it.cost_item,
							calculation_breakdown: it.calculation_breakdown, qty: qty,
							size: it.size, pack_date: it.pack_date, exp_date: it.exp_date,
							batch_no: it.batch_no, product_code: it.product_code,
							reel_length: it.reel_length, reel_width: it.reel_width,
							reel_area: it.reel_area, slit_width: it.slit_width
						});
					});
					if (!sel.length) { frappe.msgprint(__("Select at least one item.")); return; }
					frappe.call({
						method: "nxtgen_savinda_pricing_calculator.api.production_plan.add_planning_items",
						args: { production_plan: frm.doc.name, selections: JSON.stringify(sel), consolidate: (v.consolidate ? 1 : 0) },
						freeze: true, freeze_message: __("Calculating…"),
						callback: function (r2) {
							d.hide();
							frappe.show_alert({ message: __("Added {0} item(s) to planning.", [(r2.message || {}).added || 0]), indicator: "green" });
							frm.reload_doc();
						},
					});
				},
			});
			d.show();
		},
	});
}

function _show_wastage_dialog(frm) {
	var rows = (frm.doc.mr_items || []).filter(function (r) { return r.item_code; });
	if (!rows.length) {
		frappe.msgprint(__("No raw materials yet. Click 'Get Raw Materials for Production' first."));
		return;
	}

	// Default tolerance % from the costing (offset wastage %)
	frappe.db.get_single_value("Costing Configuration", "offset_wastage_pct").then(function (defpct) {
		defpct = parseFloat(defpct) || 0;

		var d = new frappe.ui.Dialog({
			title: __("Add Wastage to Raw Materials"),
			size: "large",
			fields: [
				{
					fieldtype: "Float", fieldname: "tolerance", label: "Tolerance %",
					default: defpct,
					description: "Default from costing wastage. Change it to recompute every row — you can still edit each wastage qty manually.",
				},
				{ fieldtype: "HTML", fieldname: "tbl" },
			],
			primary_action_label: __("Add Wastage to Rows"),
			primary_action: function () {
				var $w = d.fields_dict.tbl.$wrapper;
				var applied = 0;
				$w.find("input.waste-inp").each(function () {
					var idx = parseInt($(this).attr("data-idx"), 10);
					var w = parseFloat($(this).val()) || 0;
					var row = frm.doc.mr_items[idx];
					if (!row) return;
					// Idempotent: net = current qty minus any wastage already applied
					var net = (parseFloat(row.quantity) || 0) - (parseFloat(row.custom_wastage_qty) || 0);
					row.custom_wastage_qty = w;      // stored separately (for print formats)
					row.quantity = net + w;           // total = net + wastage
					if (w > 0) applied++;
				});
				d.hide();
				frm.refresh_field("mr_items");
				frm.dirty();
				frappe.show_alert({ message: applied + " material row(s) updated with wastage.", indicator: "green" });
			},
		});

		function render(pct) {
			pct = parseFloat(pct) || 0;
			var html = '<div style="max-height:52vh;overflow:auto"><table class="table table-bordered" style="font-size:12px;margin:0">'
				+ '<thead><tr><th>Item</th><th class="text-right">Net Qty</th><th>UOM</th>'
				+ '<th class="text-right">Wastage Qty (editable)</th></tr></thead><tbody>';
			rows.forEach(function (r) {
				var idx = frm.doc.mr_items.indexOf(r);
				// Net = current qty minus wastage already applied (so it's stable on re-open)
				var net = (parseFloat(r.quantity) || 0) - (parseFloat(r.custom_wastage_qty) || 0);
				var w = Math.round(net * (pct / 100) * 10000) / 10000;
				html += '<tr>'
					+ '<td>' + frappe.utils.escape_html(r.item_name || r.item_code) + '</td>'
					+ '<td class="text-right">' + net + '</td>'
					+ '<td>' + (r.uom || "") + '</td>'
					+ '<td class="text-right"><input type="number" step="0.0001" min="0" class="form-control input-sm waste-inp" '
					+ 'data-idx="' + idx + '" value="' + w + '" style="width:120px;display:inline-block"></td>'
					+ '</tr>';
			});
			html += '</tbody></table></div>';
			d.fields_dict.tbl.$wrapper.html(html);
		}

		d.show();
		render(defpct);
		// Recompute all rows when the tolerance % changes
		d.fields_dict.tolerance.$input.on("input change", function () {
			render(d.get_value("tolerance"));
		});
	});
}

function _show_base_material_request_dialog(frm) {
	frappe.call({
		method: "nxtgen_savinda_pricing_calculator.api.production_plan.get_base_material_request_rows",
		args: { production_plan: frm.doc.name },
		freeze: true,
		freeze_message: __("Loading Manufacturing Planning…"),
		callback: function (r) {
			var rows = r.message || [];
			if (!rows.length) {
				frappe.msgprint(__("There are no Manufacture rows with a Base Material in Manufacturing Planning."));
				return;
			}
			var d = new frappe.ui.Dialog({
				title: __("Re-Request Base Material"),
				size: "large",
				fields: [
					{ fieldtype: "HTML", fieldname: "manufacture_rows" },
				],
				primary_action_label: __("Calculate Materials"),
				primary_action: function () {
					var selections = [];
					d.fields_dict.manufacture_rows.$wrapper.find("tr.base-material-row").each(function () {
						var $tr = $(this);
						if (!$tr.find(".base-material-select").prop("checked")) return;
						var index = parseInt($tr.attr("data-index"), 10);
						var fg_qty = parseFloat($tr.find(".base-material-fg-qty").val()) || 0;
						if (fg_qty <= 0) return;
						selections.push({ planning_row: rows[index].planning_row, fg_qty: fg_qty });
					});
					if (!selections.length) {
						frappe.msgprint(__("Select at least one item and enter a manufacture quantity."));
						return;
					}
					frappe.call({
						method: "nxtgen_savinda_pricing_calculator.api.production_plan.preview_base_material_request",
						args: { production_plan: frm.doc.name, selections: selections },
						freeze: true,
						freeze_message: __("Calculating base materials…"),
						callback: function (preview) {
							d.hide();
							_show_base_material_request_review(frm, selections, preview.message || []);
						},
					});
				},
			});

			var html = '<p class="text-muted">Enter the Finished Good quantity to manufacture. '
				+ 'Only Manufacture rows are shown; assembly rows do not consume base material directly.</p>'
				+ '<div style="max-height:52vh;overflow:auto"><table class="table table-bordered" style="font-size:12px;margin:0">'
				+ '<thead><tr><th></th><th>FG Item</th><th class="text-right">Planned Qty</th>'
				+ '<th class="text-right">Manufacture Qty</th><th>Base Material</th></tr></thead><tbody>';
			rows.forEach(function (row, index) {
				html += '<tr class="base-material-row" data-index="' + index + '">'
					+ '<td><input class="base-material-select" type="checkbox" checked></td>'
					+ '<td>' + frappe.utils.escape_html(row.item_name || row.fg_item || "")
					+ '<br><span class="text-muted">' + frappe.utils.escape_html(row.fg_item || "") + '</span></td>'
					+ '<td class="text-right">' + row.planned_qty + '</td>'
					+ '<td><input class="form-control input-sm text-right base-material-fg-qty" type="number" min="0" max="'
					+ row.planned_qty + '" step="0.0001" value="' + row.planned_qty + '" style="width:130px"></td>'
					+ '<td>' + frappe.utils.escape_html(row.base_material_name || row.base_material || "")
					+ '<br><span class="text-muted">' + frappe.utils.escape_html(row.base_material || "") + '</span></td></tr>';
			});
			html += '</tbody></table></div>';
			d.fields_dict.manufacture_rows.$wrapper.html(html);
			d.show();
		},
	});
}

function _show_base_material_request_review(frm, selections, materials) {
	var d = new frappe.ui.Dialog({
		title: __("Confirm Base Material Re-Request"),
		size: "large",
		fields: [
			{ fieldtype: "HTML", fieldname: "material_rows" },
		],
		primary_action_label: __("Create Draft Re-Request"),
		primary_action: function () {
			var request_items = [];
			d.fields_dict.material_rows.$wrapper.find(".base-material-request-qty").each(function () {
				request_items.push({
					item_code: $(this).attr("data-item-code"),
					qty: parseFloat($(this).val()) || 0,
				});
			});
			frappe.call({
				method: "nxtgen_savinda_pricing_calculator.api.production_plan.create_base_material_request",
				args: { production_plan: frm.doc.name, selections: selections, request_items: request_items },
				freeze: true,
				freeze_message: __("Creating Material Request…"),
				callback: function (r) {
					var material_request = (r.message || {}).material_request;
					d.hide();
					frappe.msgprint({
						title: __("Base Material Re-Request Created"),
						message: __("Draft Material Request: <a href='/app/material-request/{0}' target='_blank'>{0}</a>", [material_request]),
						indicator: "green",
					});
					frm.reload_doc();
				},
			});
		},
	});
	var html = '<p class="text-muted">Required Qty is calculated from Manufacturing Planning. '
		+ 'You may change Request Qty before creating the draft Material Request.</p>'
		+ '<div style="max-height:52vh;overflow:auto"><table class="table table-bordered" style="font-size:12px;margin:0">'
		+ '<thead><tr><th>Base Material</th><th>UOM</th><th class="text-right">Calculated Qty</th>'
		+ '<th class="text-right">Request Qty</th></tr></thead><tbody>';
	materials.forEach(function (row) {
		html += '<tr><td>' + frappe.utils.escape_html(row.item_name || row.item_code || "")
			+ '<br><span class="text-muted">' + frappe.utils.escape_html(row.item_code || "") + '</span></td>'
			+ '<td>' + frappe.utils.escape_html(row.uom || "") + '</td>'
			+ '<td class="text-right">' + row.calculated_qty + '</td>'
			+ '<td><input class="form-control input-sm text-right base-material-request-qty" type="number" min="0.000001" '
			+ 'step="0.0001" data-item-code="' + frappe.utils.escape_html(row.item_code || "") + '" value="' + row.calculated_qty + '" style="width:130px"></td></tr>';
	});
	html += '</tbody></table></div>';
	d.fields_dict.material_rows.$wrapper.html(html);
	d.show();
}
