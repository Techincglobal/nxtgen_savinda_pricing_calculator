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

	// The dialog is also opened from an FG Item after a quotation is complete.
	// It therefore accepts either a Frappe form or a small form-like context.
	var source_doc = (frm && frm.doc) || {};
	var base_currency = (frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency) || "LKR";
	var currency = source_doc.currency || base_currency;
	var crate = currency === base_currency ? 1 : (parseFloat(source_doc.conversion_rate) || 0);
	var customer = source_doc.customer || "";
	var valid_from = frappe.datetime.get_today();
	var valid_upto = source_doc.valid_till || null;
	var API = "nxtgen_savinda_pricing_calculator.api.pricing_rule.";

	function rounded(v) { return Math.round(v * 1000000000) / 1000000000; }
	function conv(v) { return rounded((parseFloat(v) || 0) / (crate > 0 ? crate : 1)); }

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
						return { min_qty: t.min_qty, max_qty: t.max_qty, rate: conv(t.rate), unit_cost: conv(t.unit_cost), currency: crate > 0 ? currency : base_currency };
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
		var applied_currency = crate > 0 ? currency : base_currency;
		var applied_rate = crate > 0 ? crate : 1;
		var cur_lbl = applied_currency;
		var fx_request = 0;
		function apply_currency() {
			if (!d) return;
			var selected = d.get_value("rule_currency");
			var exchange = selected === base_currency ? 1 : parseFloat(d.get_value("exchange_rate"));
			var valid = !!selected && Number.isFinite(exchange) && exchange > 0;
			d.get_primary_btn().prop("disabled", !valid);
			d.set_df_property("exchange_rate", "read_only", selected === base_currency);
			d.set_df_property("exchange_rate", "description", base_currency + " per 1 " + (selected || "selected currency") + ". Changing this converts the currently entered tier rates.");
			usable.forEach(function (x, idx) {
				var fieldname = "tiers_" + idx;
				d.set_df_property(fieldname, "read_only", !valid);
				if (!valid) return;
				(d.get_value(fieldname) || []).forEach(function (row) {
					row.rate = rounded((parseFloat(row.rate) || 0) * applied_rate / exchange);
					row.unit_cost = rounded((parseFloat(row.unit_cost) || 0) * applied_rate / exchange);
					row.currency = selected;
				});
				var grid = d.fields_dict[fieldname].grid;
				grid.update_docfield_property("rate", "label", "Rate per Item (" + selected + ")");
				grid.update_docfield_property("unit_cost", "label", "Unit Cost (" + selected + ")");
				grid.refresh();
			});
			if (valid) { applied_currency = selected; applied_rate = exchange; }
			d.fields_dict.currency_notice.$wrapper.html(valid
				? "<p>Rates below are in <b>" + frappe.utils.escape_html(selected) + " per single item</b> — not per 1,000 items.</p>"
				: "<p class='text-warning'>Select a currency and enter a positive exchange rate to convert the prices and continue.</p>");
		}
		function currency_changed() {
			if (!d) return;
			fx_request++;
			var selected = d.get_value("rule_currency");
			d.set_value("exchange_rate", selected === base_currency ? 1 : 0);
			apply_currency();
		}
		function fetch_exchange_rate() {
			var selected = d.get_value("rule_currency");
			if (!selected) return;
			if (selected === base_currency) { d.set_value("exchange_rate", 1); apply_currency(); return; }
			var request = ++fx_request;
			frappe.call({method: "erpnext.setup.utils.get_exchange_rate", args: {
				from_currency: selected, to_currency: base_currency,
				transaction_date: d.get_value("valid_from") || frappe.datetime.get_today(),
			}, callback: function (r) {
				if (request !== fx_request || selected !== d.get_value("rule_currency")) return;
				if (Number.isFinite(parseFloat(r.message)) && parseFloat(r.message) > 0) {
					d.set_value("exchange_rate", parseFloat(r.message));
				} else frappe.msgprint("No exchange rate found. Enter " + base_currency + " per 1 " + selected + " manually.");
			}});
		}
		var fields = [{
			fieldtype: "HTML",
			options: "<div style='padding:6px 10px;background:#f0f4ff;border-radius:4px;font-size:12px;color:#1a3a5c'>"
				+ "Select the rule currency, then set quantity ranges and the selling rate <b>per single item</b> for each product. "
				+ "These are saved as <b>Pricing Rules</b>, so a Sales Order prices each line automatically by quantity. "
				+ "Use <b>Add Row</b> for a pricing-only new quantity tier; create a new calculation qty break first when its production cost must also be recalculated."
				+ (customer ? " Scoped to customer <b>" + frappe.utils.escape_html(customer) + "</b>." : " (No customer — rules apply to any customer.)")
				+ "<br><span style='color:#8a6d1f'>Max Qty = 0 means no upper limit. Ranges should not overlap. "
				+ "A higher priority wins when dated rules overlap (use 20 for a short temporary override).</span></div>",
		}];
		fields.push(
			{ fieldtype: "Section Break", label: "Pricing Currency" },
			{ fieldtype: "Link", fieldname: "rule_currency", label: "Pricing Rule Currency", options: "Currency", reqd: 1,
				default: currency, onchange: currency_changed },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Float", fieldname: "exchange_rate", label: "Exchange Rate", precision: 9, reqd: 1,
				default: crate || 0, onchange: function () { if (d) { fx_request++; apply_currency(); } } },
			{ fieldtype: "Button", fieldname: "fetch_exchange_rate", label: "Fetch Exchange Rate", click: fetch_exchange_rate },
			{ fieldtype: "Section Break" },
			{ fieldtype: "HTML", fieldname: "currency_notice" },
			{ fieldtype: "Section Break", label: "Rule Validity & Override" },
			{ fieldtype: "Date", fieldname: "valid_from", label: "Valid From", default: valid_from },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Date", fieldname: "valid_upto", label: "Valid Until", default: valid_upto,
				description: "Leave blank for no expiry." },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Int", fieldname: "priority", label: "Priority (1–20)", default: 10,
				description: "Higher priority overrides another matching rule. Use 20 for a temporary price." }
		);
		usable.forEach(function (x, idx) {
			fields.push({ fieldtype: "Section Break", label: x.item_name + "  (" + x.fg_item + ")" });
			fields.push({
				fieldtype: "Table", fieldname: "tiers_" + idx,
				cannot_add_rows: false, in_place_edit: true, data: x.tiers,
				fields: [
					{ fieldtype: "Float", fieldname: "min_qty", label: "Min Qty", in_list_view: 1, columns: 2, reqd: 1 },
					{ fieldtype: "Float", fieldname: "max_qty", label: "Max Qty (0=∞)", in_list_view: 1, columns: 2 },
					{ fieldtype: "Link", fieldname: "currency", options: "Currency", hidden: 1 },
					{ fieldtype: "Currency", fieldname: "rate", label: "Rate per Item (" + cur_lbl + ")", options: "currency", precision: 9, in_list_view: 1, columns: 3, reqd: 1 },
					{ fieldtype: "Currency", fieldname: "unit_cost", label: "Unit Cost", options: "currency", precision: 9, in_list_view: 1, columns: 2, read_only: 1 },
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
			primary_action: function (values) {
				var selected_currency = values.rule_currency;
				var exchange = selected_currency === base_currency ? 1 : parseFloat(values.exchange_rate);
				if (!selected_currency || !Number.isFinite(exchange) || exchange <= 0 || applied_currency !== selected_currency || applied_rate !== exchange) {
					frappe.msgprint("Select a currency and enter a positive exchange rate before creating rules."); return;
				}
				var rule_priority = Math.max(1, Math.min(20, parseInt(values.priority, 10) || 10));
				var jobs = usable.map(function (x, idx) { return { fg: x, rows: d.get_value("tiers_" + idx) || [] }; });
				if (jobs.some(function (job) { return !job.rows.length || job.rows.some(function (row) { return !Number.isFinite(parseFloat(row.rate)) || parseFloat(row.rate) <= 0; }); })) {
					frappe.msgprint("Enter at least one positive-rate quantity tier for every FG."); return;
				}
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
							currency: selected_currency,
							valid_from: values.valid_from || null,
							valid_upto: values.valid_upto || null,
							cost_item: j.fg.cost_item,
							priority: rule_priority,
						},
						freeze: true, freeze_message: "Creating pricing rules for " + j.fg.fg_item + "…",
						callback: function (r) { total += ((r.message || {}).count || 0); run(i + 1); },
						error: function () { run(i + 1); },
					});
				})(0);
			},
		});
		d.show();
		apply_currency();
	}
};
