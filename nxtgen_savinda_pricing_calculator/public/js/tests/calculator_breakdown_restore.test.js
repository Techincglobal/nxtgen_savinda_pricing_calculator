const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let options, request, savedDraft;
const context = {Vue:{createApp(o){options=o;return {mount(){return {};}};}},
 frappe:{pages:{'offset-calculator':{}},call(r){request=r;},show_alert(){},msgprint(){throw Error('Unexpected validation');}},
 window:{localStorage:{getItem(){return savedDraft;}}},setTimeout,clearTimeout};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../../nxtgen_savinda_pricing_calculator/page/offset_calculator/offset_calculator.js'),'utf8'),context);
context.oc_mount_app({});
const state=Object.assign(options.data(), {selectedSpecs:[],form:{ref:'CI',item_qty:100},additionalBreakdowns:[60,40],boardCountMode:'Breakdown',scheduleCalc(){},_draftKey(){return 'test';}});
Object.defineProperty(state,'calcPayload',{get(){return options.computed.calcPayload.call(state);}});
options.methods.saveCosting.call(state);
const payload=JSON.parse(request.args.payload);
assert.deepStrictEqual(payload.form.breakdown_qtys,[60,40]);
assert.equal(payload.form.board_count_mode,'Breakdown');
state.boardCountMode='Required';
assert.equal(state.calcPayload.form.breakdown_qtys.length,0);
savedDraft=JSON.stringify({form:{ref:'CI',item_qty:100},additionalBreakdowns:[60,40]});
assert.equal(options.methods.restoreDraft.call(state),true);
assert.equal(state.boardCountMode,'Breakdown');
savedDraft=JSON.stringify({form:{ref:'CI',item_qty:100},additionalBreakdowns:[60,40],boardCountMode:'Required'});
options.methods.restoreDraft.call(state);
assert.equal(state.boardCountMode,'Required');
console.log('Breakdown save payload, mode selection, and legacy/new draft restoration passed.');
