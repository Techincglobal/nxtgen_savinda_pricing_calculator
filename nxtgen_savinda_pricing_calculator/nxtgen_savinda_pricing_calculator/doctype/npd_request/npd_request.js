// Copyright (c) 2026, Techincglobal.com and contributors
// NPD Request — new-product request: create FG items, confirm BOM, create Production Plan.

frappe.ui.form.on("NPD Request", {
	refresh(frm) {
		if (frm.is_new()) return;

		var PP = "nxtgen_savinda_pricing_calculator.api.production_plan.";
		var DT = "nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.npd_request.npd_request.";

		// ── Artwork approval (same pattern as Cost Sheet) ──
		var roles = frappe.user_roles || [];
		var can_approve = roles.indexOf("Artwork Approver") >= 0 || roles.indexOf("System Manager") >= 0;
		var status = frm.doc.artwork_status || "Pending";
		var color = status === "Approved" ? "green" : (status === "Rejected" ? "red" : "orange");
		frm.dashboard.set_headline(
			'Artwork: <span class="indicator ' + color + '">' + status + "</span>"
			+ (frm.doc.artwork_approved_by ? " by " + frappe.utils.escape_html(frm.doc.artwork_approved_by) : "")
		);
		if (can_approve && status !== "Approved") {
			frm.add_custom_button(__("Approve Artwork"), function () {
				_artwork_action(frm, DT + "approve_artwork", "Approve Artwork");
			}, __("Artwork"));
		}
		if (can_approve && status !== "Rejected") {
			frm.add_custom_button(__("Reject Artwork"), function () {
				_artwork_action(frm, DT + "reject_artwork", "Reject Artwork");
			}, __("Artwork"));
		}

		// ── Actions ──
		frm.add_custom_button(__("Create FG Items"), function () {
			_fg_create_popup(frm);
		}, __("Actions"));

		frm.add_custom_button(__("Confirm BOM"), function () {
			frappe.call({
				method: PP + "confirm_bom", args: { npd_request: frm.doc.name },
				freeze: true, freeze_message: __("Confirming BOM…"),
				callback: function (r) {
					var m = r.message || {};
					if (m.ok) {
						frappe.show_alert({ message: __("BOM confirmed."), indicator: "green" });
						frm.reload_doc();
					} else {
						frappe.msgprint({
							title: __("BOM not confirmed"),
							message: __("Resolve these first:<br>• {0}", [(m.missing || []).join("<br>• ")]),
							indicator: "orange",
						});
					}
				},
			});
		}, __("Actions"));

		// Open BOM Builder from the NPD's related Cost Sheet (BOM team creates BOMs here).
		if (frm.doc.cost_sheet) {
			frm.add_custom_button(__("Open BOM Builder"), function () {
				window.open(frappe.urllib.get_full_url("/app/bom-builder?cost_sheet=" + encodeURIComponent(frm.doc.cost_sheet)));
			}, __("Actions"));
		}

		if (!frm.doc.production_plan) {
			frm.add_custom_button(__("Create Production Plan"), function () {
				// BOMs are NOT required up-front — items without a BOM are fetched onto the
				// plan and flagged for the BOM team (Open BOM Builder → Sync BOMs there).
				frappe.call({
					method: PP + "create_plan_from_npd", args: { npd_request: frm.doc.name },
					freeze: true, freeze_message: __("Creating Production Plan…"),
					callback: function (r) {
						var m = r.message || {};
						if (m.production_plan) {
							if (m.needs_bom) {
								frappe.show_alert({ message: __("Plan created. Some items need a BOM — sent to the BOM team."), indicator: "orange" });
							}
							frappe.set_route("Form", "Production Plan", m.production_plan);
						}
					},
				});
			}, __("Create"));
		} else {
			frm.add_custom_button(__("View Production Plan"), function () {
				frappe.set_route("Form", "Production Plan", frm.doc.production_plan);
			}, __("Create"));
		}

		// ── Print / PDF (format by pricing type) ──
		frm.add_custom_button(__("Download PDF"), function () {
			var url = "/api/method/frappe.utils.print_format.download_pdf?doctype=NPD+Request&name="
				+ encodeURIComponent(frm.doc.name)
				+ "&format=NPD+Job+Ticket&no_letterhead=1";
			window.open(frappe.urllib.get_full_url(url));
		}, __("Actions"));
	},
});

// FG creation popup — fetch proposed Item + Product Library details from the source,
// let the user review/edit, then create the FG(s) + Product Library.
function _fg_create_popup(frm) {
	var PP = "nxtgen_savinda_pricing_calculator.api.production_plan.";
	frappe.call({
		method: PP + "get_fg_preview", args: { npd_request: frm.doc.name }, freeze: true,
		callback: function (r) {
			var m = r.message || {};
			var lines = m.lines || [];
			if (!lines.length) {
				frappe.msgprint({ title: __("FG Items"),
					message: __("All requested lines already have an FG Item."), indicator: "blue" });
				return;
			}
			var fields = [{ fieldtype: "HTML", fieldname: "hdr", options:
				"<div style='color:#555;font-size:12px;margin-bottom:6px'>"
				+ __("Review the product details fetched from the source. Change anything if needed, then create the FG item(s) + Product Library.")
				+ "</div>" }];
			var meta = [];
			lines.forEach(function (ln, i) {
				var p = "l" + i + "__";
				meta.push({ idx: i, row_name: ln.row_name, cost_item: ln.cost_item });
				fields.push({ fieldtype: "Section Break", label: __("Product: ") + (ln.requested_name || ln.item_name) });
				fields.push({ fieldtype: "Data", fieldname: p + "item_name", label: __("Item Name"), reqd: 1, default: ln.item_name });
				fields.push({ fieldtype: "Link", options: "Item Group", fieldname: p + "item_group", label: __("Item Group"), default: ln.item_group });
				fields.push({ fieldtype: "Link", options: "Department", fieldname: p + "department", label: __("Department"), default: ln.department });
				fields.push({ fieldtype: "Link", options: "UOM", fieldname: p + "stock_uom", label: __("Stock UOM"), default: ln.stock_uom });
				fields.push({ fieldtype: "Data", fieldname: p + "customer_ref", label: __("Customer Ref"), default: ln.customer_ref });
				fields.push({ fieldtype: "Column Break" });
				fields.push({ fieldtype: "Data", fieldname: p + "pl_customer_product_code", label: __("Customer Product Code"), default: ln.pl_customer_product_code });
				fields.push({ fieldtype: "Int", fieldname: p + "pl_no_of_colors", label: __("No of Colors"), default: ln.pl_no_of_colors });
				fields.push({ fieldtype: "Int", fieldname: p + "pl_no_of_ups", label: __("No of Ups"), default: ln.pl_no_of_ups });
				fields.push({ fieldtype: "Data", fieldname: p + "pl_product_size", label: __("Product Size"), default: ln.pl_product_size });
				if (m.is_flexo) {
					fields.push({ fieldtype: "Float", fieldname: p + "pl_width_mm", label: __("Width (mm)"), default: ln.pl_width_mm });
					fields.push({ fieldtype: "Float", fieldname: p + "pl_length_mm", label: __("Length (mm)"), default: ln.pl_length_mm });
					fields.push({ fieldtype: "Data", fieldname: p + "pl_core_size", label: __("Core Size"), default: ln.pl_core_size });
				} else {
					fields.push({ fieldtype: "Data", fieldname: p + "pl_full_sheet_size", label: __("Full Sheet Size"), default: ln.pl_full_sheet_size });
					fields.push({ fieldtype: "Data", fieldname: p + "pl_cut_sheet_size", label: __("Cut Sheet Size"), default: ln.pl_cut_sheet_size });
				}
				fields.push({ fieldtype: "Data", fieldname: p + "pl_artwork_no", label: __("Artwork No"), default: ln.pl_artwork_no });
				fields.push({ fieldtype: "Data", fieldname: p + "pl_artwork_version", label: __("Artwork Version"), default: ln.pl_artwork_version });
			});
			var d = new frappe.ui.Dialog({
				title: __("Create FG Items — Review Details"), size: "large", fields: fields,
				primary_action_label: __("Create FG"),
				primary_action: function (v) {
					var details = meta.map(function (mt) {
						var p = "l" + mt.idx + "__";
						return {
							row_name: mt.row_name, cost_item: mt.cost_item,
							item_name: v[p + "item_name"], item_group: v[p + "item_group"],
							department: v[p + "department"], stock_uom: v[p + "stock_uom"],
							customer_ref: v[p + "customer_ref"],
							pl_customer_product_code: v[p + "pl_customer_product_code"],
							pl_no_of_colors: v[p + "pl_no_of_colors"], pl_no_of_ups: v[p + "pl_no_of_ups"],
							pl_product_size: v[p + "pl_product_size"],
							pl_full_sheet_size: v[p + "pl_full_sheet_size"], pl_cut_sheet_size: v[p + "pl_cut_sheet_size"],
							pl_width_mm: v[p + "pl_width_mm"], pl_length_mm: v[p + "pl_length_mm"],
							pl_core_size: v[p + "pl_core_size"],
							pl_artwork_no: v[p + "pl_artwork_no"], pl_artwork_version: v[p + "pl_artwork_version"],
						};
					});
					frappe.call({
						method: PP + "create_fg_items",
						args: { npd_request: frm.doc.name, details: JSON.stringify(details) },
						freeze: true, freeze_message: __("Creating FG Items…"),
						callback: function (r2) {
							d.hide();
							var mm = r2.message || {};
							frappe.show_alert({ message: __("FG created: ") + ((mm.created || []).join(", ") || "—"), indicator: "green" });
							frm.reload_doc();
						},
					});
				},
			});
			d.show();
		},
	});
}

function _artwork_action(frm, method, title) {
	var d = new frappe.ui.Dialog({
		title: __(title),
		fields: [{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") }],
		primary_action_label: __(title),
		primary_action: function (values) {
			frappe.call({
				method: method,
				args: { npd_request: frm.doc.name, remarks: values.remarks || "" },
				freeze: true,
				callback: function () { d.hide(); frm.reload_doc(); frappe.show_alert({ message: __("Done."), indicator: "green" }); },
			});
		},
	});
	d.show();
}
