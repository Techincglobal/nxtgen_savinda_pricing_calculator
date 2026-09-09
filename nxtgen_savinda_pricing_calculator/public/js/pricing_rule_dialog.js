// Qty-pricing popup shown after FG creation. Pre-fills qty tiers + rates from the costing,
// lets the user edit ranges/rates, then writes native ERPNext Pricing Rules so a Sales Order
// prices each line automatically by qty (instead of copying the rate from the quotation).
window.nxtgen_pricing = window.nxtgen_pricing || {};

// frm      — Savinda Quotation form (customer / currency / conversion_rate / valid_till)
// fg_items — [{item_code, item_name, cost_item}] of the just-created FGs
// on_done  — called after rules are created or skipped
nxtgen_pricing.showForFGs = function (frm, fg_items, on_done) {
	on_done = on_done || function () {};
	if (!fg_items || !fg_items.length) { on_done(); return; }

	var currency = frm.doc.currency || "";
	var crate = parseFloat(frm.doc.conversion_rate) || 1;
	var customer = frm.doc.customer || "";
	var valid_from = frappe.datetime.get_today();
	var valid_upto = frm.doc.valid_till || null;
	var API = "nxtgen_savinda_pricing_calculator.api.pricing_rule.";

	function conv(v) { return Math.round(((parseFloat(v) || 0) / crate) * 100) / 100; }

	var loaded = [];
	(function fetch(i) {
		if (i >= fg_items.length) { build(); return; }
		frappe.call({
			method: API + "get_qty_price_tiers",
			args: { fg_item: fg_items[i].item_code },
			callback: function (r) {
				var m = r.message || {};
				loaded.push({
					fg_item: fg_items[i].item_code,
					item_name: fg_items[i].item_name || m.item_name || fg_items[i].item_code,
					cost_item: m.cost_item || fg_items[i].cost_item || "",
					tiers: (m.tiers || []).map(function (t) {
						return { min_qty: t.min_qty, max_qty: t.max_qty, rate: conv(t.rate), unit_cost: conv(t.unit_cost) };
					}),
				});
				fetch(i + 1);
			},
			error: function () { loaded.push({ fg_item: fg_items[i].item_code, item_name: fg_items[i].item_name, cost_item: fg_items[i].cost_item || "", tiers: [] }); fetch(i + 1); },
		});
	})(0);

	function build() {
		var usable = loaded.filter(function (x) { return x.tiers.length; });
		if (!usable.length) {
			frappe.show_alert({ message: "No qty tiers found to price (no cost breakdown / qty breaks).", indicator: "orange" });
			on_done();
			return;
		}
		var cur_lbl = currency || "LKR";
		var fields = [{
			fieldtype: "HTML",
			options: "<div style='padding:6px 10px;background:#f0f4ff;border-radius:4px;font-size:12px;color:#1a3a5c'>"
				+ "Set the qty ranges and selling rate (in <b>" + frappe.utils.escape_html(cur_lbl) + "</b>) for each product. "
				+ "These are saved as <b>Pricing Rules</b>, so a Sales Order prices each line automatically by quantity."
				+ (customer ? " Scoped to customer <b>" + frappe.utils.escape_html(customer) + "</b>." : " (No customer — rules apply to any customer.)")
				+ "<br><span style='color:#8a6d1f'>Max Qty = 0 means no upper limit. Ranges should not overlap.</span></div>",
		}];
		usable.forEach(function (x, idx) {
			fields.push({ fieldtype: "Section Break", label: x.item_name + "  (" + x.fg_item + ")" });
			fields.push({
				fieldtype: "Table", fieldname: "tiers_" + idx,
				cannot_add_rows: false, in_place_edit: true, data: x.tiers,
				fields: [
					{ fieldtype: "Float", fieldname: "min_qty", label: "Min Qty", in_list_view: 1, columns: 2, reqd: 1 },
					{ fieldtype: "Float", fieldname: "max_qty", label: "Max Qty (0=∞)", in_list_view: 1, columns: 2 },
					{ fieldtype: "Currency", fieldname: "rate", label: "Rate (" + cur_lbl + ")", in_list_view: 1, columns: 3, reqd: 1 },
					{ fieldtype: "Currency", fieldname: "unit_cost", label: "Unit Cost", in_list_view: 1, columns: 2, read_only: 1 },
				],
			});
		});

		var d = new frappe.ui.Dialog({
			title: "Qty Pricing Rules — " + usable.length + " FG(s)",
			size: "extra-large",
			fields: fields,
			primary_action_label: "Create Pricing Rules",
			secondary_action_label: "Skip",
			secondary_action: function () { d.hide(); on_done(); },
			primary_action: function () {
				var jobs = usable.map(function (x, idx) { return { fg: x, rows: d.get_value("tiers_" + idx) || [] }; });
				d.hide();
				var total = 0;
				(function run(i) {
					if (i >= jobs.length) {
						frappe.show_alert({ message: "Created " + total + " pricing rule(s).", indicator: "green" });
						on_done();
						return;
					}
					var j = jobs[i];
					frappe.call({
						method: API + "create_qty_pricing_rules",
						args: {
							fg_item: j.fg.fg_item,
							tiers: JSON.stringify(j.rows),
							customer: customer,
							currency: currency,
							valid_from: valid_from,
							valid_upto: valid_upto,
							cost_item: j.fg.cost_item,
						},
						freeze: true, freeze_message: "Creating pricing rules for " + j.fg.fg_item + "…",
						callback: function (r) { total += ((r.message || {}).count || 0); run(i + 1); },
						error: function () { run(i + 1); },
					});
				})(0);
			},
		});
		d.show();
	}
};
