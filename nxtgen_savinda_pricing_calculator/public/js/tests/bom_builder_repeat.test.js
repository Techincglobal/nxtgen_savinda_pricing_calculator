const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let options;
const context = {
	frappe: { pages: { 'bom-builder': {} } },
	Vue: { createApp(config) { options = config; return { mount() { return {}; } }; } },
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname,
	'../../../nxtgen_savinda_pricing_calculator/page/bom_builder/bom_builder.js'), 'utf8'), context);
context.bb_mount_app({});

function model(pricingType) {
	return Object.assign({}, options.data(), options.methods, {
		pricingType, selectedFG: 'FG', itemQty: 10000, mfgQty: 10000,
		baseMat: 'FILM', baseMatQty: 79.2, baseMatUom: 'sqmt',
		sheetData: { bom_repeat_count: 2500, required_reel_length_m: 240 },
		operations: [
			{ spec_name: 'PRINT', output_qty: 240, output_uom: 'Meter' },
			{ spec_name: 'EXCLUDED', exclude_from_bom: true, split_to_item_unit: true },
			{ spec_name: 'CUT', output_qty: 240, output_uom: 'Meter' },
			{ spec_name: 'PACK', output_is_fg: true, output_qty: 2500 },
		],
	});
}

const flexo = model('Flexo');
flexo.syncChain();
assert.equal(flexo.operations[0].input_qty, 79.2);
assert.equal(flexo.operations[0].input_uom, 'sqmt');
assert.equal(flexo.operations[0].output_qty, 240);
assert.equal(flexo.operations[0].output_uom, 'Meter');
assert(flexo.operations[0].sfg_code.endsWith('-MTR-SFG'));
assert.equal(flexo.operations[2].input_item_code, flexo.operations[0].sfg_code);
assert.equal(flexo.operations[2].input_qty, 240);
assert.equal(flexo.operations[2].output_qty, 240);
assert.equal(flexo.operations[3].input_qty, 240);
assert.equal(flexo.operations[3].output_qty, 10000);

// Split converts metres to pieces at the selected operation.
flexo.operations[2].split_to_item_unit = true;
flexo.syncChain();
assert.equal(flexo.operations[2].input_qty, 240);
assert.equal(flexo.operations[2].output_qty, 10000);
assert.equal(flexo.operations[2].output_uom, 'Nos');
assert(flexo.operations[2].sfg_code.endsWith('-PCS-SFG'));
assert.equal(flexo.operations[3].input_qty, 10000);

assert(!options.template.includes('v-if="pricingType !== \'Flexo\'" :class="[\'bb-split-btn\''));

// Offset retains its cut-sheet flow and explicit split behavior.
const offset = model('Offset');
offset.baseMatQty = 1375;
offset.baseMatUom = 'Nos';
offset.operations = [
	{ spec_name: 'PRINT', output_qty: 2750, output_uom: 'Nos' },
	{ spec_name: 'CUT', output_qty: 2750, output_uom: 'Nos', split_to_item_unit: true },
];
offset.syncChain();
assert.equal(offset.operations[0].input_qty, 1375);
assert.equal(offset.operations[0].output_qty, 2750);
assert(!offset.operations[0].sfg_code.includes('-RPT-'));
assert.equal(offset.operations[1].input_qty, 2750);
assert.equal(offset.operations[1].output_qty, 10000);
console.log('Flexo area-to-metres-to-pieces, excluded operations and unchanged Offset flow verified.');
