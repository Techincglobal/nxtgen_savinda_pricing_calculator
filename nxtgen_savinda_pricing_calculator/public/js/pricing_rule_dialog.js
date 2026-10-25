// Shared FG / Product Library quantity-pricing editor. Every tier has its own currency.
window.nxtgen_pricing = window.nxtgen_pricing || {};

nxtgen_pricing.showForFGs = function (frm, fg_items, on_done) {
	on_done = on_done || function () {};
	if (!fg_items || !fg_items.length) { on_done(); return; }
	var source = (frm && frm.doc) || {};
	var base = (frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency) || "LKR";
	var initial_currency = source.currency || base;
	var initial_exchange = initial_currency === base ? 1 : (parseFloat(source.conversion_rate) || 0);
	var API = "nxtgen_savinda_pricing_calculator.api.pricing_rule.";
	var loaded = [], requests = new WeakMap(), d, closed = false;
	function rounded(value) { return Math.round(value * 1000000000) / 1000000000; }
	function exchange(row) { return row.currency === base ? 1 : parseFloat(row.exchange_rate); }
	function valid_exchange(row) { return !!row.currency && Number.isFinite(exchange(row)) && exchange(row) > 0; }
	function ready(row) {
		return valid_exchange(row) && row.applied_currency === row.currency &&
			parseFloat(row.applied_exchange_rate) === exchange(row);
	}
	function invalidate_request(row) { requests.set(row, (requests.get(row) || 0) + 1); }
	function convert(row) {
		if (!valid_exchange(row)) return;
		var old_rate = parseFloat(row.applied_exchange_rate) || 1;
		var new_rate = exchange(row);
		row.rate = rounded((parseFloat(row.rate) || 0) * old_rate / new_rate);
		row.unit_cost = rounded((parseFloat(row.unit_cost) || 0) * old_rate / new_rate);
		row.exchange_rate = new_rate;
		row.applied_currency = row.currency;
		row.applied_exchange_rate = new_rate;
	}
	function rows(idx) { return d.get_value("tiers_" + idx) || []; }
	function refresh() {
		if (!d) return;
		var can_create = true;
		loaded.forEach(function (fg, idx) {
			var data = rows(idx);
			if (!data.length || data.some(function (row) {
				return !ready(row) || !Number.isFinite(parseFloat(row.rate)) || parseFloat(row.rate) <= 0;
			})) can_create = false;
			var grid = d.fields_dict["tiers_" + idx].grid;
			grid.refresh();
			(grid.grid_rows || []).forEach(function (grid_row) {
				if (grid_row.toggle_editable && grid_row.doc) grid_row.toggle_editable("rate", ready(grid_row.doc));
			});
		});
		d.get_primary_btn().prop("disabled", !can_create);
	}
	function row_currency_changed() {
		var row = this.doc;
		if (!d || !row) return;
		invalidate_request(row);
		row.exchange_rate = row.currency === base ? 1 : 0;
		convert(row);
		refresh();
	}
	function row_exchange_changed() {
		var row = this.doc;
		if (!d || !row) return;
		invalidate_request(row);
		convert(row);
		refresh();
	}
	function fetch_missing_rates() {
		loaded.forEach(function (fg, idx) {
			rows(idx).forEach(function (row) {
				// Keep rates users already entered. Fetch only rows awaiting conversion.
				if (!row.currency || row.currency === base || ready(row)) return;
				invalidate_request(row);
				var token = requests.get(row), currency = row.currency;
				frappe.call({method: "erpnext.setup.utils.get_exchange_rate", args: {
					from_currency: currency, to_currency: base,
					transaction_date: d.get_value("valid_from") || frappe.datetime.get_today(),
				}, callback: function (r) {
					if (closed || requests.get(row) !== token || row.currency !== currency || rows(idx).indexOf(row) < 0) return;
					var value = parseFloat(r.message);
					if (Number.isFinite(value) && value > 0) {
						row.exchange_rate = value;
						convert(row); refresh();
					} else frappe.msgprint("No exchange rate found. Enter " + base + " per 1 " + currency + " in the pricing row.");
				}});
			});
		});
	}
	function build() {
		loaded = loaded.filter(function (fg) { return fg.tiers.length; });
		if (!loaded.length) {
			frappe.show_alert({message: "No quantity tiers found to price.", indicator: "orange"});
			on_done(); return;
		}
		var fields = [
			{fieldtype: "HTML", options: "<p>Select <b>Currency</b> directly in each pricing row. " +
				"Enter the exchange rate as <b>" + frappe.utils.escape_html(base) + " per 1 selected currency</b>, or fetch missing rates. " +
				"Rates are <b>per single item</b>, not per 1,000. Changing a row's currency requires a new exchange rate before its rate can be edited. " +
				(customer_text()) + "</p>"},
			{fieldtype: "Button", fieldname: "fetch_exchange_rates", label: "Fetch Missing Exchange Rates", click: fetch_missing_rates},
			{fieldtype: "Section Break", label: "Rule Validity & Override"},
			{fieldtype: "Date", fieldname: "valid_from", label: "Valid From", default: frappe.datetime.get_today()},
			{fieldtype: "Column Break"},
			{fieldtype: "Date", fieldname: "valid_upto", label: "Valid Until", default: source.valid_till || null,
				description: "Leave blank for no expiry."},
			{fieldtype: "Column Break"},
			{fieldtype: "Int", fieldname: "priority", label: "Priority (1–20)", default: 10,
				description: "Higher priority overrides matching dated rules. Max Qty = 0 means no upper limit."},
		];
		loaded.forEach(function (fg, idx) {
			fields.push({fieldtype: "Section Break", label: fg.item_name + " (" + fg.fg_item + ")"});
			fields.push({fieldtype: "Table", fieldname: "tiers_" + idx, data: fg.tiers,
				cannot_add_rows: false, in_place_edit: true,
				on_add_row: function () {
					rows(idx).forEach(function (row) {
						if (!row.applied_currency) {
							row.currency = row.currency || initial_currency;
							row.exchange_rate = row.currency === base ? 1 : initial_exchange;
							row.applied_currency = row.exchange_rate > 0 ? row.currency : base;
							row.applied_exchange_rate = row.exchange_rate > 0 ? row.exchange_rate : 1;
						}
					});
					refresh();
				},
				fields: [
					{fieldtype: "Float", fieldname: "min_qty", label: "Min Qty", in_list_view: 1, columns: 1, reqd: 1},
					{fieldtype: "Float", fieldname: "max_qty", label: "Max Qty (0=∞)", in_list_view: 1, columns: 1},
					{fieldtype: "Link", fieldname: "currency", label: "Currency", options: "Currency",
						default: initial_currency, in_list_view: 1, columns: 2, reqd: 1, onchange: row_currency_changed},
					{fieldtype: "Float", fieldname: "exchange_rate", label: "Exchange Rate", precision: 9,
						default: initial_exchange, in_list_view: 1, columns: 2, reqd: 1, onchange: row_exchange_changed,
						description: base + " per 1 row currency. Use 1 for " + base + "."},
					{fieldtype: "Currency", fieldname: "rate", label: "Rate per Item", options: "currency",
						precision: 9, in_list_view: 1, columns: 2, reqd: 1, onchange: refresh},
					{fieldtype: "Currency", fieldname: "unit_cost", label: "Unit Cost", options: "currency",
						precision: 9, in_list_view: 1, columns: 2, read_only: 1},
					{fieldtype: "Data", fieldname: "applied_currency", hidden: 1},
					{fieldtype: "Float", fieldname: "applied_exchange_rate", hidden: 1, precision: 9},
				]});
		});
		d = new frappe.ui.Dialog({
			title: "Qty Pricing Rules — " + loaded.length + " FG(s)", size: "extra-large", fields: fields,
			primary_action_label: "Create Pricing Rules", secondary_action_label: "Skip",
			secondary_action: function () { closed = true; d.hide(); on_done(); },
			primary_action: function (values) {
				var jobs = [];
				for (var i = 0; i < loaded.length; i++) {
					var data = rows(i), groups = {};
					if (!data.length || data.some(function (row) {
						return !ready(row) || !Number.isFinite(parseFloat(row.rate)) || parseFloat(row.rate) <= 0;
					})) {
						frappe.msgprint("Select Currency and enter a positive Exchange Rate and Rate per Item in every pricing row.");
						return;
					}
					data.forEach(function (row) {
						if (!groups[row.currency]) groups[row.currency] = [];
						groups[row.currency].push({min_qty: row.min_qty, max_qty: row.max_qty, rate: row.rate});
					});
					Object.keys(groups).forEach(function (currency) {
						jobs.push({fg: loaded[i], currency: currency, rows: groups[currency]});
					});
				}
				closed = true; d.hide();
				var total = 0, priority = Math.max(1, Math.min(20, parseInt(values.priority, 10) || 10));
				(function run(idx) {
					if (idx >= jobs.length) {
						frappe.show_alert({message: "Created " + total + " pricing rule(s).", indicator: "green"});
						on_done(); return;
					}
					var job = jobs[idx];
					frappe.call({method: API + "create_qty_pricing_rules", args: {
						fg_item: job.fg.fg_item, cost_item: job.fg.cost_item, currency: job.currency,
						tiers: JSON.stringify(job.rows), customer: source.customer || "",
						valid_from: values.valid_from || null, valid_upto: values.valid_upto || null, priority: priority,
					}, freeze: true, freeze_message: "Creating pricing rules for " + job.fg.fg_item + " (" + job.currency + ")…",
					callback: function (r) { total += ((r.message || {}).count || 0); run(idx + 1); },
					error: function () { run(idx + 1); }});
				})(0);
			},
		});
		d.show(); refresh();
	}
	function customer_text() {
		return source.customer ? "Rules are scoped to <b>" + frappe.utils.escape_html(source.customer) + "</b>." :
			"No customer selected — rules apply to any customer.";
	}
	(function fetch(idx) {
		if (idx >= fg_items.length) { build(); return; }
		frappe.call({method: API + "get_qty_price_tiers", args: {fg_item: fg_items[idx].item_code},
			callback: function (r) {
				var info = r.message || {}, applied_rate = initial_exchange > 0 ? initial_exchange : 1;
				loaded.push({fg_item: fg_items[idx].item_code,
					item_name: fg_items[idx].item_name || info.item_name || fg_items[idx].item_code,
					cost_item: info.cost_item || fg_items[idx].cost_item || "",
					tiers: (info.tiers || []).map(function (tier) {
						return {min_qty: tier.min_qty, max_qty: tier.max_qty, currency: initial_currency,
							exchange_rate: initial_exchange, rate: rounded((parseFloat(tier.rate) || 0) / applied_rate),
							unit_cost: rounded((parseFloat(tier.unit_cost) || 0) / applied_rate),
							applied_currency: initial_exchange > 0 ? initial_currency : base, applied_exchange_rate: applied_rate};
					})});
				fetch(idx + 1);
			}, error: function () { fetch(idx + 1); }});
	})(0);
};
