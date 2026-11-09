const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const handlers = {}, requests = [], parents = {};
const context = {
	__: value => value,
	locals: {},
	frappe: {
		ui: { form: { on(dt, events) { handlers[dt] = Object.assign(handlers[dt] || {}, events); } } },
		call(request) { requests.push(request); return Promise.resolve(); },
		model: { async set_value(dt, name, field, value) {
			const row = context.locals[dt][name];
			const values = typeof field === 'object' ? field : { [field]: value };
			for (const [key, next] of Object.entries(values)) {
				if (row[key] === next) continue;
				row[key] = next;
				if ((handlers[dt] || {})[key]) await handlers[dt][key](parents[name], dt, name);
			}
		} }
	}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../production_plan.js'), 'utf8'), context);

function setup(dt, name, pricing) {
	const row = { name, doctype: dt, fg_item: 'FG', qty: 10000, bom_no: 'BOM-NEW',
		batch_no: 'BATCH', exp_date: '2026-12-01', expiry_date: 'DEC 2030' };
	context.locals[dt] = context.locals[dt] || {};
	context.locals[dt][name] = row;
	const frm = { doc: { name: 'PP', docstatus: 0, custom_pricing_type: pricing } };
	parents[name] = frm;
	return { frm, row };
}

(async () => {
	const dt = 'Production Planning Item';
	const { frm, row } = setup(dt, 'OFFSET', 'Offset');
	await handlers[dt].bom_no(frm, dt, row.name);
	assert.equal(requests.at(-1).args.bom_no, 'BOM-NEW');
	await requests.at(-1).callback({ message: { bom_no: 'BOM-NEW', ups: 8, cuts: 2,
		cut_sheet_size: '15.5 x 21.5', full_sheet_qty: 1313, base_material: 'NEW-PAPER' } });
	assert.equal(row.ups, 8);
	assert.equal(row.cut_sheet_size, '15.5 x 21.5');
	assert.equal(row.full_sheet_qty, 1313);
	assert.equal(row.batch_no, 'BATCH');
	assert.equal(row.expiry_date, 'DEC 2030');

	await handlers[dt].qty(frm, dt, row.name);
	const stale = requests.at(-1);
	row.qty = 5000;
	await handlers[dt].qty(frm, dt, row.name);
	await requests.at(-1).callback({ message: { full_sheet_qty: 675 } });
	await stale.callback({ message: { full_sheet_qty: 1313 } });
	assert.equal(row.full_sheet_qty, 675);

	const count = requests.length;
	row.fg_item = 'FG-2';
	await handlers[dt].fg_item(frm, dt, row.name);
	assert.equal(requests.length, count + 1);
	assert.equal(row.bom_no, '');
	assert.equal(requests.at(-1).args.fg_item, 'FG-2');
	assert.equal(requests.at(-1).args.bom_no, '');
	await requests.at(-1).callback({ message: { bom_no: 'FG-2-BOM', ups: 12 } });
	assert.equal(row.bom_no, 'FG-2-BOM');
	assert.equal(requests.length, count + 1); // Programmatic BOM changes cannot loop.

	const flex = setup('Flexo Production Planning Item', 'FLEXO', 'Flexo');
	await handlers[flex.row.doctype].bom_no(flex.frm, flex.row.doctype, flex.row.name);
	await requests.at(-1).callback({ message: { ups: 4, reel_width: 330, reel_length: 200, reel_area: 79.2 } });
	assert.equal(flex.row.ups, 4);
	assert.equal(flex.row.reel_area, 79.2);
	assert.equal(flex.row.qty, 10000);
	assert.equal(flex.row.exp_date, '2026-12-01');

	const manual = setup(dt, 'MANUAL', 'Offset');
	Object.assign(manual.row, {cuts: 4, wastage: 100, full_sheet_qty: 200,
		required_cut_sheet_qty: 500, cut_sheet_qty: 600, cut_sheet_qty_includes_wastage: 1});
	await handlers[dt].full_sheet_qty(manual.frm, dt, manual.row.name);
	assert.equal(manual.row.cut_sheet_qty, 800);
	assert.equal(manual.row.required_cut_sheet_qty, 700);
	assert.equal(manual.row.full_sheet_qty, 200);
	manual.row.wastage = 120;
	await handlers[dt].wastage(manual.frm, dt, manual.row.name);
	assert.equal(manual.row.cut_sheet_qty, 820);
	assert.equal(manual.row.full_sheet_qty, 205);

	row.fg_item = '';
	await handlers[dt].fg_item(frm, dt, row.name);
	assert.equal(row.bom_no, '');
	assert.equal(row.full_sheet_qty, 0);
	console.log('Offset/Flexo BOM and qty refresh, FG reset, stale-response guards and preserved dates verified.');
})().catch(error => { console.error(error); process.exitCode = 1; });
