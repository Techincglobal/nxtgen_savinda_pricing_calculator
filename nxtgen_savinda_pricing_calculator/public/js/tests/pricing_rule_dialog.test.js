// Run with: node public/js/tests/pricing_rule_dialog.test.js (from the app package).
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const source = fs.readFileSync(path.join(__dirname, "..", "pricing_rule_dialog.js"), "utf8");

function open(sourceDoc = {}) {
	const calls = [], messages = [], requests = [];
	let dialog;
	class Dialog {
		constructor(options) {
			this.options = options; this.values = {}; this.fields_dict = {}; this.disabled = false;
			options.fields.forEach(field => {
				if (!field.fieldname) return;
				this.values[field.fieldname] = field.data || field.default;
				this.fields_dict[field.fieldname] = {$wrapper: {html: text => { this.notice = text; }},
					grid: {update_docfield_property() {}, refresh() {}}};
			});
			dialog = this;
		}
		get_value(name) { return this.values[name]; }
		set_value(name, value) {
			const changed = this.values[name] !== value;
			this.values[name] = value;
			const field = this.options.fields.find(f => f.fieldname === name);
			if (changed && field.onchange) field.onchange();
		}
		set_df_property() {}
		get_primary_btn() { return {prop: (name, value) => { if (name === "disabled") this.disabled = value; }}; }
		show() {}
		hide() { this.hidden = true; }
	}
	const context = {console, frappe: {
		boot: {sysdefaults: {currency: "LKR"}}, datetime: {get_today: () => "2026-10-25"},
		utils: {escape_html: value => value}, ui: {Dialog},
		msgprint: value => messages.push(value), show_alert() {},
		call(options) {
			calls.push(options);
			if (options.method.endsWith("get_qty_price_tiers")) options.callback({message: {tiers: [
				{min_qty: 1, max_qty: 0, rate: 24.554, unit_cost: 18.7808}
			]}});
			else if (options.method.endsWith("get_exchange_rate")) requests.push(options);
			else if (options.method.endsWith("create_qty_pricing_rules")) options.callback({message: {count: 1}});
		},
	}};
	context.window = context;
	vm.createContext(context); vm.runInContext(source, context);
	context.nxtgen_pricing.showForFGs({doc: sourceDoc}, [{item_code: "FG-1", item_name: "Test FG"}]);
	return {dialog, calls, messages, requests,
		row: () => dialog.get_value("tiers_0")[0],
		set_row: (name, value, idx = 0) => {
			const row = dialog.get_value("tiers_0")[idx];
			row[name] = value;
			const field = dialog.options.fields.find(f => f.fieldname === "tiers_0").fields.find(f => f.fieldname === name);
			if (field.onchange) field.onchange.call({doc: row});
		},
		submit: () => dialog.options.primary_action({...dialog.values}),
		fetch: () => dialog.options.fields.find(f => f.fieldname === "fetch_exchange_rates").click()};
}

{
	const t = open({customer: "CUSTOMER"});
	assert.equal(t.row().rate, 24.554);
	const currency = t.dialog.options.fields.find(f => f.fieldname === "tiers_0").fields.find(f => f.fieldname === "currency");
	assert.equal(currency.in_list_view, 1);
	assert(!currency.hidden);
	t.set_row("currency", "USD");
	assert.equal(t.dialog.disabled, true);
	t.submit();
	assert.equal(t.calls.filter(c => c.method.endsWith("create_qty_pricing_rules")).length, 0);
	t.set_row("exchange_rate", 300);
	assert.equal(t.dialog.disabled, false);
	assert.equal(t.row().rate, 0.081846667);
	t.submit();
	const create = t.calls.find(c => c.method.endsWith("create_qty_pricing_rules"));
	assert.equal(create.args.currency, "USD");
	assert.equal(create.args.customer, "CUSTOMER");
	assert.equal(JSON.parse(create.args.tiers)[0].rate, 0.081846667);
}
{
	const t = open({currency: "USD", conversion_rate: 300});
	assert.equal(t.row().rate, 0.081846667); // no second conversion on dialog open
	t.row().rate = 0.1; // user edits a per-item rate
	t.set_row("currency", "EUR");
	t.set_row("exchange_rate", 350);
	assert.equal(t.row().rate, 0.085714286);
	assert.equal(t.row().currency, "EUR");
}
{
	const t = open();
	t.set_row("currency", "USD"); t.fetch();
	t.set_row("currency", "EUR");
	t.requests[0].callback({message: 300});
	assert.equal(t.row().exchange_rate, 0); // stale USD response ignored
	t.set_row("exchange_rate", 350);
	t.set_row("currency", "LKR");
	assert.equal(t.row().exchange_rate, 1);
	assert(Math.abs(t.row().rate - 24.554) < 0.000001);
}
{
	const t = open();
	t.set_row("currency", "USD"); t.fetch();
	t.set_row("exchange_rate", 310);
	t.requests[0].callback({message: 300});
	assert.equal(t.row().exchange_rate, 310); // user's override preserved
}
{
	const t = open();
	t.dialog.get_value("tiers_0").push({...t.row()});
	t.set_row("currency", "USD");
	t.set_row("exchange_rate", 300);
	t.submit();
	const jobs = t.calls.filter(c => c.method.endsWith("create_qty_pricing_rules"));
	assert.equal(jobs.length, 2);
	assert.equal(jobs[0].args.currency, "USD");
	assert.equal(jobs[1].args.currency, "LKR");
	assert.equal(JSON.parse(jobs[0].args.tiers)[0].rate, 0.081846667);
	assert.equal(JSON.parse(jobs[1].args.tiers)[0].rate, 24.554);
}
console.log("Pricing-rule popup checks passed: currency conversion, precision, scope, invalid-rate blocking and stale-request protection.");
