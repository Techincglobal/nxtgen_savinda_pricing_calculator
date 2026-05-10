// Copyright (c) 2026, Techincglobal.com and contributors
// Offset Calculator — Frappe 15 Desk Page
// Route: /app/offset-calculator

frappe.pages['offset-calculator'].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Offset Calculator',
		single_column: true,
	});
	var mountEl = document.createElement('div');
	mountEl.id = 'oc-mount';
	page.main[0].appendChild(mountEl);
	oc_inject_styles();
	oc_mount_app(mountEl);
};

frappe.pages['offset-calculator'].on_page_show = function (wrapper) {
	if (window.__oc_app__) window.__oc_app__.checkUrlRef();
};

// ─────────────────────────────────────────────────────────────
//  APP
// ─────────────────────────────────────────────────────────────

function oc_mount_app(el) {

	var API = {
		getSpecs: 'nxtgen_savinda_pricing_calculator.api.offset_calculator.get_specs',
		getMachines: 'nxtgen_savinda_pricing_calculator.api.offset_calculator.get_machines',
		calculate: 'nxtgen_savinda_pricing_calculator.api.offset_calculator.calculate',
		save: 'nxtgen_savinda_pricing_calculator.api.offset_calculator.save_costing',
		load: 'nxtgen_savinda_pricing_calculator.api.offset_calculator.load_costing',
	};

	function debounce(fn, ms) {
		var t;
		return function () { var a = arguments, ctx = this; clearTimeout(t); t = setTimeout(function () { fn.apply(ctx, a); }, ms); };
	}
	function fmtCur(v) {
		return parseFloat(v || 0).toLocaleString('en-LK', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
	}
	function fmtNum(v) { return Math.round(v || 0).toLocaleString('en-LK'); }

	var app = Vue.createApp({

		data() {
			return {
				loading: true,
				calcLoading: false,
				saveLoading: false,
				calcError: '',
				savedDocName: '',
				costSheetRef: '',  // Cost Sheet to return to when clicking Back

				allSpecs: [],
				allMachines: [],

				// Selected machine object (not just name)
				selectedMachine: null,
				// machineSpecState[cost_fact] = { selected_item, attr_values, rate, req_qty }
				machineSpecState: {},

				selectedSpecNames: [],
				specState: {},
				autoSelectOperations: [],
				needsAutoSelect: false,

				// Material link-field
				matSearch: '', matResults: [], matOpen: false,
				matLoading: false, matHighlight: 0, matTimer: null,

				form: {
					// Common
					pricing_type: 'Offset',
					customer_name: '', ref: '', price_list: '', carton_size: '',
					base_material: '', material_rate: 0,
					no_of_colors: 4, item_qty: 1000,
					profit_margin: 15, tax_sscl: true, tax_vat: false,
					// Offset-specific
					full_sheet_l: 0, full_sheet_w: 0,
					cut_sheet_l: 0, cut_sheet_w: 0,
					no_of_cuts: 2, no_of_ups: 4,
					// Flexo-specific
					reel_width_mm: 0,
					product_width_mm: 0, product_length_mm: 0,
					product_margin_mm: 4, product_gap_mm: 3,
				},

				calc: { sheet: null, cost_rows: [], group_totals: {}, pricing: null },
			};
		},

		computed: {
			selectedSpecs() {
				var n = this.selectedSpecNames;
				return this.allSpecs.filter(function (s) { return n.indexOf(s.spec_name) > -1; });
			},
			specsByGroup() {
				var g = {};
				this.allSpecs.forEach(function (s) {
					var gr = s.group || 'Other';
					if (!g[gr]) g[gr] = [];
					g[gr].push(s);
				});
				return g;
			},
			isOffset() { return this.form.pricing_type === 'Offset'; },
			isFlexo() { return this.form.pricing_type === 'Flexo'; },

			// Auto-include material row check
			materialReady() {
				return !!(this.form.base_material && this.form.material_rate && this.form.item_qty);
			},

			// Flexo reel requirements card — show when flexo calc done
			flexoReady() {
				return this.isFlexo && this.calc.sheet && this.calc.sheet.reel_area > 0;
			},
			calcPayload() {
				var self = this;
				var specs = this.selectedSpecs.map(function (spec) {
					var facts = (spec.cost_facts || []).map(function (cf) {
						var st = (self.specState[spec.spec_name] || {})[cf.cost_fact] || {};
						var hasItems = cf.master && cf.master.items && cf.master.items.length > 0;
						var rate = parseFloat(st.rate || 0);
						if (!rate && !hasItems) rate = parseFloat(self.form.material_rate || 0);
						return {
							cost_fact: cf.cost_fact, is_primary: cf.is_primary,
							selected_item: st.selected_item || '',
							attribute_values: st.attr_values || {},
							rate: rate, req_qty: parseFloat(st.req_qty || 0),
						};
					});
					return { spec_name: spec.spec_name, cost_facts: facts };
				});

				// Machine spec — same structure as a regular spec
				var machine_spec = null;
				if (this.selectedMachine) {
					var mfacts = (this.selectedMachine.cost_facts || []).map(function (cf) {
						var st = self.machineSpecState[cf.cost_fact] || {};
						return {
							cost_fact: cf.cost_fact, is_primary: cf.is_primary,
							selected_item: st.selected_item || '',
							attribute_values: st.attr_values || {},
							rate: parseFloat(st.rate || 0),
							req_qty: parseFloat(st.req_qty || 0),
						};
					});
					machine_spec = {
						spec_name: this.selectedMachine.spec_name,
						group: "Machine",
						cost_facts: mfacts,
					};
				}
				return { form: this.form, selected_specs: specs, machine_spec: machine_spec };
			},
		},

		mounted() {
			// Read URL params before loadData so specs/machines load for the right type
			var urlParams = new URLSearchParams(window.location.search);
			var pt = urlParams.get('pricing_type');
			if (pt === 'Flexo' || pt === 'Offset') {
				this.form.pricing_type = pt;
			}
			var ops = urlParams.get('operations');
			if (ops) {
				// URLSearchParams.get() already decodes the value; split on | separator
				this.autoSelectOperations = ops.split('|').filter(Boolean);
			}
			this.loadData();
		},

		methods: {

			checkUrlRef() {
				// Read ?ref=CB-xxx from URL — survives page refresh
				var urlParams = new URLSearchParams(window.location.search);
				var name = urlParams.get('ref') || urlParams.get('name') || '';

				// Also check frappe.route_options (set by frappe.set_route)
				var opts = frappe.route_options || {};
				name = name || opts.name || opts.ref || '';
				frappe.route_options = {};

				// Also store cost_sheet ref for back button
				var cs = urlParams.get('cost_sheet') || opts.cost_sheet || '';
				if (cs) this.costSheetRef = cs;

				if (name && name !== this.savedDocName) {
					this.savedDocName = name;
					this.loadExisting(name);
				}
			},

			loadData() {
				var self = this;
				var pt = self.form.pricing_type || 'Offset';
				var done = 0;
				function check() { done++; if (done >= 2) { self.loading = false; self.checkUrlRef(); } }
				frappe.call({ method: API.getSpecs, args: { pricing_type: pt }, callback: function (r) {
					self.allSpecs = r.message || [];
					// on_page_show may have run _autoSelectFromOperations before specs loaded; retry now
					if (self.needsAutoSelect) { self.needsAutoSelect = false; self._autoSelectFromOperations(); }
					check();
				}});
				frappe.call({ method: API.getMachines, args: { pricing_type: pt }, callback: function (r) { self.allMachines = r.message || []; check(); } });
			},

			// Reload specs when pricing type changes
			onPricingTypeChange() {
				var self = this;
				self.loading = true;
				self.selectedSpecNames = [];
				self.specState = {};
				self.selectedMachine = null;
				self.machineSpecState = {};
				self.calc = { sheet: null, cost_rows: [], group_totals: {}, pricing: null };
				var pt = self.form.pricing_type || 'Offset';
				var done = 0;
				function check() { done++; if (done >= 2) { self.loading = false; } }
				frappe.call({ method: API.getSpecs, args: { pricing_type: pt }, callback: function (r) { self.allSpecs = r.message || []; check(); } });
				frappe.call({ method: API.getMachines, args: { pricing_type: pt }, callback: function (r) { self.allMachines = r.message || []; check(); } });
			},

			// ── Machine ──
			selectMachine(machineName) {
				var self = this;
				if (!machineName) {
					self.selectedMachine = null;
					self.machineSpecState = {};
					self.scheduleCalc();
					return;
				}
				var m = this.allMachines.find(function (x) { return x.spec_name === machineName; });
				self.selectedMachine = m || null;
				self.machineSpecState = {};
				if (m) {
					(m.cost_facts || []).forEach(function (cf) {
						var items = (cf.master && cf.master.items) || [];
						var sel = items.length === 1 ? items[0].item : '';
						var rate = (items.length === 1 && items[0].is_fix_rate) ? (items[0].rate || 0) : 0;
						self.machineSpecState[cf.cost_fact] = { selected_item: sel, attr_values: {}, rate: rate, req_qty: 0 };
					});
				}
				this.scheduleCalc();
			},

			// ── Specs (checkboxes) ──
			toggleSpec(specName) {
				var idx = this.selectedSpecNames.indexOf(specName);
				if (idx > -1) {
					this.selectedSpecNames.splice(idx, 1);
					delete this.specState[specName];
				} else {
					this.selectedSpecNames.push(specName);
					this.initSpecState(specName);
				}
				this.scheduleCalc();
			},
			isSelected(n) { return this.selectedSpecNames.indexOf(n) > -1; },

			initSpecState(specName) {
				var self = this;
				var spec = this.allSpecs.find(function (s) { return s.spec_name === specName; });
				if (!spec) return;
				var state = {};
				(spec.cost_facts || []).forEach(function (cf) {
					var items = (cf.master && cf.master.items) || [];
					var sel = items.length === 1 ? items[0].item : '';
					var rate = (items.length === 1 && items[0].is_fix_rate) ? (items[0].rate || 0) : 0;
					state[cf.cost_fact] = { selected_item: sel, attr_values: {}, rate: rate, req_qty: 0 };
					if (sel && !(items[0] && items[0].is_fix_rate)) self.fetchItemRate(specName, cf.cost_fact, sel);
				});
				this.specState[specName] = state;
			},

			// Auto-select Finishing specs whose operation matches the inquiry's operations list.
			// May be called before allSpecs is loaded (race with on_page_show); if so, set a
			// flag so loadData's getSpecs callback retries once specs are available.
			_autoSelectFromOperations() {
				var self = this;
				var ops = self.autoSelectOperations;
				if (!ops.length) return;
				if (!self.allSpecs.length) {
					self.needsAutoSelect = true;
					return;
				}
				self.allSpecs.forEach(function (spec) {
					if (spec.group !== 'Finishing') return;
					if (!spec.operation || ops.indexOf(spec.operation) === -1) return;
					if (self.selectedSpecNames.indexOf(spec.spec_name) === -1) {
						self.selectedSpecNames.push(spec.spec_name);
						self.initSpecState(spec.spec_name);
					}
				});
			},

			getSpecState(sn, cf) { return (this.specState[sn] || {})[cf] || {}; },
			getMachineState(cf) { return this.machineSpecState[cf] || {}; },

			onItemSelect(specName, costFact, itemName, masterItems) {
				var st = (this.specState[specName] || {})[costFact];
				if (!st) return;
				st.selected_item = itemName;
				var m = (masterItems || []).find(function (i) { return i.item === itemName; });
				if (m && m.is_fix_rate && m.rate) { st.rate = m.rate; this.scheduleCalc(); }
				else if (itemName) this.fetchItemRate(specName, costFact, itemName);
			},

			onMachineItemSelect(costFact, itemName, masterItems) {
				var st = this.machineSpecState[costFact];
				if (!st) return;
				st.selected_item = itemName;
				var m = (masterItems || []).find(function (i) { return i.item === itemName; });
				if (m && m.is_fix_rate && m.rate) { st.rate = m.rate; this.scheduleCalc(); }
			},

			fetchItemRate(specName, costFact, itemCode) {
				if (!itemCode) return;
				var self = this;
				var filters = { item_code: itemCode, selling: 1 };
				if (self.form.price_list) filters.price_list = self.form.price_list;
				frappe.call({
					method: 'frappe.client.get_value',
					args: { doctype: 'Item Price', filters: filters, fieldname: 'price_list_rate' },
					callback: function (r) {
						var rate = r.message && r.message.price_list_rate;
						var st = (self.specState[specName] || {})[costFact];
						if (st) { st.rate = parseFloat(rate || 0); self.scheduleCalc(); }
					},
				});
			},

			onAttrChange(specName, costFact, attrName, value, attrType) {
				var st = (this.specState[specName] || {})[costFact];
				if (!st) return;
				if (!st.attr_values) st.attr_values = {};
				st.attr_values[attrName] = attrType === 'Number' ? (parseFloat(value) || 0) : value;
				this.scheduleCalc();
			},

			onMachineAttrChange(costFact, attrName, value, attrType) {
				var st = this.machineSpecState[costFact];
				if (!st) return;
				if (!st.attr_values) st.attr_values = {};
				st.attr_values[attrName] = attrType === 'Number' ? (parseFloat(value) || 0) : value;
				this.scheduleCalc();
			},

			getAttrValue(sn, cf, a) { return ((this.specState[sn] || {})[cf] || {}).attr_values && ((this.specState[sn] || {})[cf] || {}).attr_values[a] || ''; },
			getMachineAttrValue(cf, a) { return (this.machineSpecState[cf] || {}).attr_values && (this.machineSpecState[cf] || {}).attr_values[a] || ''; },

			// ── Calculate ──
			scheduleCalc: debounce(function () { this.calculate(); }, 500),

			calculate() {
				var self = this;
				if (!self.form.item_qty) return;
				self.calcLoading = true; self.calcError = '';
				frappe.call({
					method: API.calculate,
					args: { payload: JSON.stringify(self.calcPayload) },
					callback: function (r) {
						self.calcLoading = false;
						if (r.message) {
							self.calc = {
								sheet: r.message.sheet || {},
								cost_rows: r.message.cost_rows || [],
								group_totals: r.message.group_totals || {},
								pricing: r.message.pricing || {},
							};
							// Back-fill rates into specState
							(r.message.cost_rows || []).forEach(function (row) {
								if (row.is_auto) return;
								var st = (self.specState[row.spec_name] || {})[row.cost_fact];
								if (st) { if (row.rate) st.rate = row.rate; if (row.req_qty) st.req_qty = row.req_qty; }
							});
						}
					},
					error: function () { self.calcLoading = false; self.calcError = 'Calculation failed.'; },
				});
			},

			// ── Save ──
			saveCosting() {
				var self = this;
				if (!self.form.customer_name && !self.form.ref) {
					frappe.msgprint({ title: 'Required', message: 'Enter Customer Name or Ref.', indicator: 'orange' });
					return;
				}
				self.saveLoading = true;
				frappe.call({
					method: API.save,
					args: {
						payload: JSON.stringify({
							form: self.form,
							selected_specs: self.calcPayload.selected_specs,
							machine_spec: self.calcPayload.machine_spec,
							calc_result: self.calc,
							doc_name: self.savedDocName || '',
						})
					},
					callback: function (r) {
						self.saveLoading = false;
						if (r.message && r.message.doc_name) {
							self.savedDocName = r.message.doc_name;
							frappe.show_alert({ message: 'Saved: ' + self.savedDocName, indicator: 'green' });
							// Notify parent Cost Item to sync unit_cost from this CB
							self.notifyCostItemRefresh(self.savedDocName);
						}
					},
					error: function () { self.saveLoading = false; },
				});
			},

			openDoc() { if (this.savedDocName) frappe.set_route('Form', 'Calculation Breakdown', this.savedDocName); },

			// Back to Cost Sheet
			goBack() {
				if (this.costSheetRef) {
					frappe.set_route('Form', 'Cost Sheet', this.costSheetRef);
				} else {
					window.history.back();
				}
			},

			// After saving CB, call our whitelisted API to sync unit_cost on linked Cost Items
			notifyCostItemRefresh(cbName) {
				if (!cbName) return;
				frappe.call({
					method: 'nxtgen_savinda_pricing_calculator.api.offset_calculator.sync_cost_item_unit_cost',
					args: { calculation_breakdown: cbName },
					callback: function (r) {
						if (r.message && r.message.updated) {
							frappe.show_alert({
								message: 'Unit cost synced for ' + r.message.updated + ' item(s)',
								indicator: 'green',
							});
						}
					},
				});
			},

			// ── Load existing ──
			loadExisting(name) {
				var self = this;
				frappe.call({
					method: API.load,
					args: { name: name },
					callback: function (r) {
						if (!r.message) return;
						var d = r.message;
						self.savedDocName = d.doc_name || '';
						if (d.form) {
							Object.assign(self.form, d.form);
							// Restore matSearch display
							if (d.form.base_material) {
								var iname = frappe.db ? null : null;
								frappe.call({
									method: 'frappe.client.get_value',
									args: { doctype: 'Item', filters: { name: d.form.base_material }, fieldname: 'item_name' },
									callback: function (r2) {
										self.matSearch = (r2.message && r2.message.item_name) || d.form.base_material;
									},
								});
							}
						}
						// Restore machine
						// allMachines is already loaded before loadExisting is called
						// (loadData waits for both getSpecs+getMachines before calling checkUrlRef)
						self.selectedMachine = null;
						self.machineSpecState = {};
						if (d.machine_spec && d.machine_spec.spec_name) {
							var mSpec = d.machine_spec.spec_name;
							var m = self.allMachines.find(function (x) { return x.spec_name === mSpec; });
							if (m) {
								self.selectedMachine = m;
								(d.machine_spec.cost_facts || []).forEach(function (cf) {
									self.machineSpecState[cf.cost_fact] = {
										selected_item: cf.selected_item || '',
										attr_values: cf.attribute_values || {},
										rate: parseFloat(cf.rate || 0),
										req_qty: parseFloat(cf.req_qty || 0),
									};
								});
							} else {
								// allMachines not yet populated — wait and retry once
								setTimeout(function () {
									var m2 = self.allMachines.find(function (x) { return x.spec_name === mSpec; });
									if (m2) {
										self.selectedMachine = m2;
										(d.machine_spec.cost_facts || []).forEach(function (cf) {
											self.machineSpecState[cf.cost_fact] = {
												selected_item: cf.selected_item || '',
												attr_values: cf.attribute_values || {},
												rate: parseFloat(cf.rate || 0),
												req_qty: parseFloat(cf.req_qty || 0),
											};
										});
									}
								}, 1000);
							}
						}
						// Restore specs
						self.selectedSpecNames = [];
						self.specState = {};
						(d.selected_specs || []).forEach(function (spec) {
							self.selectedSpecNames.push(spec.spec_name);
							var state = {};
							(spec.cost_facts || []).forEach(function (cf) {
								state[cf.cost_fact] = {
									selected_item: cf.selected_item || '',
									attr_values: cf.attribute_values || {},
									rate: parseFloat(cf.rate || 0),
									req_qty: parseFloat(cf.req_qty || 0),
								};
							});
							self.specState[spec.spec_name] = state;
						});
						// Auto-select finishing specs from inquiry (only for new CBs with no saved specs)
						if (!(d.selected_specs && d.selected_specs.length) && self.autoSelectOperations.length) {
							self._autoSelectFromOperations();
						}

						// Restore stored calculation — show as-is, no recalculation
						if (d.calc_result && d.calc_result.cost_rows && d.calc_result.cost_rows.length) {
							self.calc = {
								sheet: d.calc_result.sheet || {},
								cost_rows: d.calc_result.cost_rows || [],
								group_totals: d.calc_result.group_totals || {},
								pricing: d.calc_result.pricing || {},
							};
						}
					},
				});
			},

			// ── Material link-field ──
			onMatInput() {
				var self = this; self.matHighlight = 0;
				if (self.form.base_material && self.matSearch !== self.form.base_material) {
					self.form.base_material = ''; self.form.material_rate = 0;
				}
				clearTimeout(self.matTimer);
				if (!self.matSearch.trim()) { self.matResults = []; self.matOpen = false; return; }
				self.matTimer = setTimeout(function () { self.fetchMaterials(self.matSearch); }, 300);
			},
			onMatFocus() { if (this.matResults.length) { this.matOpen = true; return; } this.fetchMaterials(this.matSearch || ''); },
			onMatBlur() { var self = this; setTimeout(function () { self.matOpen = false; }, 200); },
			onMatDown() { if (!this.matOpen) { this.matOpen = true; return; } this.matHighlight = Math.min(this.matHighlight + 1, this.matResults.length - 1); },
			onMatUp() { this.matHighlight = Math.max(this.matHighlight - 1, 0); },
			onMatEnter() { if (this.matOpen && this.matResults[this.matHighlight]) this.pickMaterial(this.matResults[this.matHighlight]); },
			closeMatDrop() { this.matOpen = false; },

			fetchMaterials(q) {
				var self = this; self.matLoading = true;
				frappe.call({
					method: 'frappe.client.get_list',
					args: { doctype: 'Item', filters: [['disabled', '=', 0], ['is_stock_item', '=', 1], ['item_name', 'like', '%' + q + '%']], fields: ['name', 'item_name', 'valuation_rate'], limit: 20 },
					callback: function (r) { self.matLoading = false; self.matResults = r.message || []; self.matOpen = true; self.matHighlight = 0; },
				});
			},

			pickMaterial(item) {
				var self = this;
				self.matOpen = false;
				self.form.base_material = item.name;
				// Show item_name as display text, store item.name (code) as value
				self.matSearch = item.item_name || item.name;
				if (self.form.price_list) {
					frappe.call({
						method: 'frappe.client.get_value',
						args: { doctype: 'Item Price', filters: { item_code: item.name, price_list: self.form.price_list, selling: 1 }, fieldname: 'price_list_rate' },
						callback: function (r) {
							self.form.material_rate = parseFloat((r.message && r.message.price_list_rate) || item.valuation_rate || 0);
							self.scheduleCalc();
						},
					});
				} else {
					self.form.material_rate = parseFloat(item.valuation_rate || 0);
					self.scheduleCalc();
				}
			},
			clearMaterial() {
				this.form.base_material = ''; this.form.material_rate = 0;
				this.matSearch = ''; this.matResults = []; this.matOpen = false;
				this.scheduleCalc();
			},

			fmtCur, fmtNum,
		},

		// ── TEMPLATE ────────────────────────────────────────────────
		template: `
<div class="oc-wrap">

  <!-- ═══════ LEFT PANEL ═══════ -->
  <div class="oc-panel">
    <div class="oc-ph oc-ph-split">
      <span class="oc-pt">Order Details</span>
      <div class="oc-type-toggle">
        <button class="oc-type-btn" :class="{active: isOffset}" @click="form.pricing_type='Offset'; onPricingTypeChange()">OFFSET</button>
        <button class="oc-type-btn" :class="{active: isFlexo}"  @click="form.pricing_type='Flexo';  onPricingTypeChange()">FLEXO</button>
      </div>
    </div>
    <div class="oc-pb">

      <div class="oc-2col">
        <div class="oc-field">
          <label class="oc-lbl">Customer Name</label>
          <input v-model="form.customer_name" type="text" class="oc-inp" />
        </div>
        <div class="oc-field">
          <label class="oc-lbl">Ref / Inquiry No</label>
          <input v-model="form.ref" type="text" class="oc-inp" />
        </div>
      </div>
      <div class="oc-2col">
        <div class="oc-field">
          <label class="oc-lbl">Price List</label>
          <input v-model="form.price_list" type="text" class="oc-inp" placeholder="e.g. Standard Selling" @change="scheduleCalc" />
        </div>
        <div class="oc-field">
          <label class="oc-lbl">Carton Size</label>
          <input v-model="form.carton_size" type="text" class="oc-inp" />
        </div>
      </div>

      <!-- ══ MATERIAL (auto-adds paper/reel cost) ══ -->
      <div class="oc-auto-card">
        <div class="oc-auto-card-title">
          <span class="oc-auto-dot" :class="materialReady ? 'on' : ''"></span>
          {{ isFlexo ? 'Reel Material' : 'Base Material' }}
          <span v-if="materialReady" class="oc-auto-ok">✓ auto-included</span>
        </div>
        <div class="oc-field">
          <label class="oc-lbl oc-lbl-blue">{{ isFlexo ? 'Reel Material (Item)' : 'Item' }}</label>
          <div class="mlf">
            <div class="mlf-row">
              <input v-model="matSearch" type="text" class="oc-inp mlf-inp"
                placeholder="Type item name…" autocomplete="off"
                @input="onMatInput" @focus="onMatFocus" @blur="onMatBlur"
                @keydown.down.prevent="onMatDown" @keydown.up.prevent="onMatUp"
                @keydown.enter.prevent="onMatEnter" @keydown.esc="closeMatDrop" />
              <button v-if="form.base_material" class="mlf-x" @click="clearMaterial" title="Clear">✕</button>
              <span v-if="matLoading" class="mlf-spin"></span>
            </div>
            <!-- Selected: show item_name (display) + item code (secondary) -->
            <div v-if="form.base_material && !matOpen" class="mlf-badge">
              <span class="mlf-badge-check">✓</span>
              <span class="mlf-badge-name">{{ matSearch }}</span>
              <span class="mlf-badge-code">{{ form.base_material }}</span>
            </div>
            <div v-if="matOpen" class="mlf-drop">
              <div v-if="matResults.length===0&&!matLoading" class="mlf-empty">No items found</div>
              <!-- Dropdown: item_name as main label, item code as small badge -->
              <div v-for="(item,idx) in matResults" :key="item.name"
                class="mlf-row-item" :class="{active:matHighlight===idx}"
                @mousedown.prevent="pickMaterial(item)">
                <span class="mlf-ri-name">{{ item.item_name || item.name }}</span>
                <div class="mlf-ri-meta">
                  <span class="mlf-ri-code">{{ item.name }}</span>
                  <span v-if="item.valuation_rate" class="mlf-ri-rate">LKR {{ item.valuation_rate.toLocaleString() }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="oc-field">
          <label class="oc-lbl">Rate (LKR / {{ isFlexo ? 'm²' : 'full sheet' }})</label>
          <input v-model.number="form.material_rate" type="number" min="0" step="0.01" class="oc-inp" @change="scheduleCalc" />
        </div>
      </div>

      <!-- ══ MACHINE (auto-adds plates + printing costs) ══ -->
      <div class="oc-auto-card">
        <div class="oc-auto-card-title">
          <span class="oc-auto-dot" :class="selectedMachine ? 'on' : ''"></span>
          Printing Machine
          <span v-if="selectedMachine" class="oc-auto-ok">✓ auto-included</span>
        </div>
        <div class="oc-field">
          <label class="oc-lbl oc-lbl-blue">Machine</label>
          <select class="oc-inp oc-sel"
            :value="selectedMachine ? selectedMachine.spec_name : ''"
            @change="selectMachine($event.target.value)">
            <option value="">— No machine —</option>
            <option v-for="m in allMachines" :key="m.spec_name" :value="m.spec_name">
              {{ m.spec_name }}
            </option>
          </select>
        </div>
        <!-- Machine's additional cost fact inputs (if any) -->
        <div v-if="selectedMachine && selectedMachine.cost_facts && selectedMachine.cost_facts.length">
          <div v-for="cf in selectedMachine.cost_facts" :key="cf.cost_fact" class="oc-cf">
            <!-- Multi-item selector for machine extra cost facts -->
            <div v-if="cf.master && cf.master.items && cf.master.items.length > 1" class="oc-field">
              <label class="oc-sublbl">{{ cf.cost_fact }}</label>
              <select class="oc-inp oc-sel"
                :value="getMachineState(cf.cost_fact).selected_item"
                @change="onMachineItemSelect(cf.cost_fact, $event.target.value, cf.master.items)">
                <option value="">— Select —</option>
                <option v-for="item in cf.master.items" :key="item.item" :value="item.item">
                  {{ item.item_name || item.item }}{{ item.is_fix_rate && item.rate ? ' — LKR '+item.rate.toLocaleString() : '' }}
                </option>
              </select>
            </div>
            <div v-if="cf.is_primary && cf.master && cf.master.attributes && cf.master.attributes.length" class="oc-attrs">
              <div v-for="attr in cf.master.attributes" :key="attr.attribute_name" class="oc-attr-field">
                <label class="oc-attr-lbl">{{ attr.lable || attr.attribute_name }}</label>
                <input :type="attr.type==='Number'?'number':'text'" min="0" class="oc-inp"
                  :value="getMachineAttrValue(cf.cost_fact, attr.attribute_name)"
                  @input="onMachineAttrChange(cf.cost_fact, attr.attribute_name, $event.target.value, attr.type)" />
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ══ OFFSET: Sheet Specs ══ -->
      <div v-if="isOffset">
        <div class="oc-divider-label">Sheet Specifications (Inches)</div>
        <div class="oc-2col">
          <div class="oc-field">
            <label class="oc-lbl">Full Sheet L × W</label>
            <div class="oc-2col-inner">
              <input v-model.number="form.full_sheet_l" type="number" min="0" class="oc-inp" placeholder="L" @change="scheduleCalc" />
              <input v-model.number="form.full_sheet_w" type="number" min="0" class="oc-inp" placeholder="W" @change="scheduleCalc" />
            </div>
          </div>
          <div class="oc-field">
            <label class="oc-lbl">Cut Sheet L × W</label>
            <div class="oc-2col-inner">
              <input v-model.number="form.cut_sheet_l" type="number" min="0" class="oc-inp" placeholder="L" @change="scheduleCalc" />
              <input v-model.number="form.cut_sheet_w" type="number" min="0" class="oc-inp" placeholder="W" @change="scheduleCalc" />
            </div>
          </div>
        </div>
        <div class="oc-3col">
          <div class="oc-field"><label class="oc-lbl">No of Cuts</label><input v-model.number="form.no_of_cuts" type="number" min="1" class="oc-inp" @change="scheduleCalc" /></div>
          <div class="oc-field"><label class="oc-lbl">No of Ups</label><input v-model.number="form.no_of_ups" type="number" min="1" class="oc-inp" @change="scheduleCalc" /></div>
          <div class="oc-field"><label class="oc-lbl">No of Colors</label><input v-model.number="form.no_of_colors" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
        </div>
        <div class="oc-2col">
          <div class="oc-field"><label class="oc-lbl">Order Quantity</label><input v-model.number="form.item_qty" type="number" min="1" class="oc-inp" @change="scheduleCalc" /></div>
          <div class="oc-field"><label class="oc-lbl">Profit Margin (%)</label><input v-model.number="form.profit_margin" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
        </div>
      </div>

      <!-- ══ FLEXO: Label & Reel Specs ══ -->
      <div v-if="isFlexo">
        <div class="oc-divider-label">Material Dimensions (mm)</div>
        <div class="oc-field">
          <label class="oc-lbl">Reel Width (mm)</label>
          <input v-model.number="form.reel_width_mm" type="number" min="0" class="oc-inp" @change="scheduleCalc" />
        </div>

        <div class="oc-divider-label">Product / Label Dimensions (mm)</div>
        <div class="oc-2col">
          <div class="oc-field"><label class="oc-lbl">Width</label><input v-model.number="form.product_width_mm" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
          <div class="oc-field"><label class="oc-lbl">Length</label><input v-model.number="form.product_length_mm" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
        </div>
        <div class="oc-2col">
          <div class="oc-field"><label class="oc-lbl">Margin (mm)</label><input v-model.number="form.product_margin_mm" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
          <div class="oc-field"><label class="oc-lbl">Gap (mm)</label><input v-model.number="form.product_gap_mm" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
        </div>
        <div class="oc-2col">
          <div class="oc-field"><label class="oc-lbl">No of Colors</label><input v-model.number="form.no_of_colors" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
          <div class="oc-field"><label class="oc-lbl">Order Quantity (stickers)</label><input v-model.number="form.item_qty" type="number" min="1" class="oc-inp" @change="scheduleCalc" /></div>
        </div>
        <div class="oc-2col">
          <div class="oc-field"><label class="oc-lbl">Profit Margin (%)</label><input v-model.number="form.profit_margin" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
        </div>
      </div>
      <div class="oc-field">
        <label class="oc-lbl">Taxes</label>
        <div class="oc-chk-row">
          <label class="oc-chk-lbl"><input type="checkbox" v-model="form.tax_sscl" class="oc-chk" @change="scheduleCalc" /><span>SSCL (2.5%)</span></label>
          <label class="oc-chk-lbl"><input type="checkbox" v-model="form.tax_vat"  class="oc-chk" @change="scheduleCalc" /><span>VAT (18%)</span></label>
        </div>
      </div>

      <!-- ══ ADDITIONAL COST ITEMS (checkboxes) ══ -->
      <div class="oc-divider"></div>
      <div class="oc-divider-label">Additional Cost Items</div>
      <div v-if="loading" class="oc-loading">Loading…</div>
      <div v-else>
        <div v-for="(specs, groupName) in specsByGroup" :key="groupName" class="oc-group">
          <div class="oc-group-label">{{ groupName }}</div>
          <div v-for="spec in specs" :key="spec.spec_name" class="oc-spec">
            <label class="oc-spec-label" :class="{active: isSelected(spec.spec_name)}">
              <input type="checkbox" class="oc-chk" :checked="isSelected(spec.spec_name)" @change="toggleSpec(spec.spec_name)" />
              <span class="oc-spec-name">{{ spec.spec_name }}</span>
            </label>
            <div v-if="isSelected(spec.spec_name)" class="oc-spec-detail">
              <div v-for="cf in spec.cost_facts" :key="cf.cost_fact" class="oc-cf">
                <div v-if="spec.cost_facts.length > 1" class="oc-cf-title">{{ cf.cost_fact }}</div>
                <!-- Item selector: item_name as display, item code as stored value -->
                <div v-if="cf.master && cf.master.items && cf.master.items.length > 1" class="oc-field">
                  <label class="oc-sublbl">Select Option</label>
                  <select class="oc-inp oc-sel"
                    :value="getSpecState(spec.spec_name, cf.cost_fact).selected_item"
                    @change="onItemSelect(spec.spec_name, cf.cost_fact, $event.target.value, cf.master.items)">
                    <option value="">— Select —</option>
                    <option v-for="item in cf.master.items" :key="item.item" :value="item.item">
                      {{ item.item_name || item.item }}{{ item.is_fix_rate && item.rate ? ' — LKR '+item.rate.toLocaleString() : '' }}
                    </option>
                  </select>
                </div>
                <!-- Single item auto-selected -->
                <div v-else-if="cf.master && cf.master.items && cf.master.items.length === 1" class="oc-cf-auto">
                  <span class="oc-sublbl">{{ cf.cost_fact }}:</span>
                  <span class="oc-cf-item">{{ cf.master.items[0].item_name || cf.master.items[0].item }}</span>
                  <span v-if="cf.master.items[0].is_fix_rate" class="oc-cf-rate">Fixed LKR {{ cf.master.items[0].rate.toLocaleString() }}</span>
                </div>
                <!-- Attribute inputs -->
                <div v-if="cf.is_primary && cf.master && cf.master.attributes && cf.master.attributes.length" class="oc-attrs">
                  <div v-for="attr in cf.master.attributes" :key="attr.attribute_name" class="oc-attr-field">
                    <label class="oc-attr-lbl">{{ attr.lable || attr.attribute_name }}</label>
                    <input :type="attr.type==='Number'?'number':'text'" min="0" class="oc-inp"
                      :value="getAttrValue(spec.spec_name, cf.cost_fact, attr.attribute_name)"
                      @input="onAttrChange(spec.spec_name, cf.cost_fact, attr.attribute_name, $event.target.value, attr.type)" />
                  </div>
                </div>
                <div v-if="cf.master && !cf.master.items.length && !cf.master.attributes.length && cf.master.calculation" class="oc-cf-info">ℹ️ {{ cf.master.calculation }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>

  <!-- ═══════ RIGHT PANEL ═══════ -->
  <div class="oc-panel">
    <div class="oc-ph oc-ph-split">
      <div style="display:flex;align-items:center;gap:8px">
        <button v-if="costSheetRef" class="oc-btn oc-btn-back" @click="goBack">← Back to Cost Sheet</button>
        <span class="oc-pt">Price Calculation</span>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <span v-if="calcLoading" class="oc-spin"></span>
        <button class="oc-btn oc-btn-green" @click="calculate" :disabled="calcLoading">⚡ {{ calcLoading ? 'Calculating…' : 'Calculate' }}</button>
        <button class="oc-btn oc-btn-blue"  @click="saveCosting" :disabled="saveLoading">💾 {{ saveLoading ? 'Saving…' : 'Save' }}</button>
      </div>
    </div>
    <div class="oc-pb">

      <div v-if="savedDocName" class="oc-saved-banner">
        ✓ Saved as <strong>{{ savedDocName }}</strong>
        <button class="oc-link-btn" @click="openDoc">Open Document →</button>
      </div>

      <!-- Offset: Sheet requirements -->
      <div v-if="calc.sheet && calc.sheet.full_sheet_qty" class="oc-result-card">
        <div class="oc-rc-title">Sheet Requirements</div>
        <div class="oc-kv-grid">
          <div class="oc-kv"><span class="k">Cut Sheet Ups</span><span class="v">{{ calc.sheet.cut_sheet_ups }}</span></div>
          <div class="oc-kv"><span class="k">Cut Sheet Qty</span><span class="v">{{ fmtNum(calc.sheet.cut_sheet_qty) }}</span></div>
          <div class="oc-kv"><span class="k">Wastage</span><span class="v">{{ fmtNum(calc.sheet.wastage) }}</span></div>
          <div class="oc-kv"><span class="k">Req. Cut Sheets</span><span class="v">{{ fmtNum(calc.sheet.req_cut_sheets) }}</span></div>
          <div class="oc-kv"><span class="k">Full Sheet Qty</span><span class="v oc-v-blue">{{ fmtNum(calc.sheet.full_sheet_qty) }}</span></div>
        </div>
      </div>

      <!-- Flexo: Reel requirements -->
      <div v-if="calc.sheet && calc.sheet.reel_area" class="oc-result-card">
        <div class="oc-rc-title">Reel Requirements</div>
        <div class="oc-kv-grid">
          <div class="oc-kv"><span class="k">Ups</span><span class="v">{{ calc.sheet.ups }}</span></div>
          <div class="oc-kv"><span class="k">Stickers / Reel</span><span class="v">{{ fmtNum(calc.sheet.stickers_per_reel) }}</span></div>
          <div class="oc-kv"><span class="k">Reel Length (m)</span><span class="v">{{ fmtNum(calc.sheet.reel_length) }}</span></div>
          <div class="oc-kv"><span class="k">Net Reel Area (m²)</span><span class="v">{{ fmtNum(calc.sheet.reel_area_net) }}</span></div>
          <div class="oc-kv"><span class="k">Setup Metrage (m)</span><span class="v">{{ calc.sheet.setup_metrage }}</span></div>
          <div class="oc-kv"><span class="k">Wastage %</span><span class="v">{{ (calc.sheet.wastage_pct * 100).toFixed(0) }}%</span></div>
          <div class="oc-kv"><span class="k">Wastage Area (m²)</span><span class="v">{{ fmtNum(calc.sheet.wastage_area) }}</span></div>
          <div class="oc-kv"><span class="k">Total Reel Area (m²)</span><span class="v oc-v-blue">{{ fmtNum(calc.sheet.reel_area) }}</span></div>
        </div>
      </div>

      <!-- Cost breakdown -->
      <div v-if="calc.cost_rows && calc.cost_rows.length" class="oc-result-card">
        <div class="oc-rc-title">Cost Breakdown</div>
        <div class="oc-tbl-scroll">
          <table class="oc-tbl">
            <thead>
              <tr>
                <th>Category</th>
                <th>Cost Item</th>
                <th>Group</th>
                <th>Item / Option</th>
                <th class="r">Qty</th>
                <th class="r">Rate</th>
                <th class="r">Amount (LKR)</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in calc.cost_rows" :key="row.spec_name+row.cost_fact" :class="row.is_auto ? 'oc-auto-row' : ''">
                <td>{{ row.spec_name }}<span v-if="row.is_auto" class="oc-auto-tag">auto</span></td>
                <td>{{ row.cost_fact }}</td>
                <td class="oc-grp">{{ row.cost_group }}</td>
                <!-- Display: item_name (human readable) as main, code as small badge -->
                <td>
                  <span v-if="row.selected_item_name && row.selected_item_name !== row.selected_item">
                    {{ row.selected_item_name }}
                    <span class="oc-code-badge">{{ row.selected_item }}</span>
                  </span>
                  <span v-else>{{ row.selected_item }}</span>
                </td>
                <td class="r mono">{{ row.req_qty }}</td>
                <td class="r mono">{{ fmtCur(row.rate) }}</td>
                <td class="r mono">{{ fmtCur(row.amount) }}</td>
              </tr>
              <tr class="oc-sub" v-if="calc.group_totals.material"><td colspan="6">Material Subtotal</td><td class="r mono">{{ fmtCur(calc.group_totals.material) }}</td></tr>
              <tr class="oc-sub" v-if="calc.group_totals.preparation"><td colspan="6">Preparation Subtotal</td><td class="r mono">{{ fmtCur(calc.group_totals.preparation) }}</td></tr>
              <tr class="oc-sub" v-if="calc.group_totals.production"><td colspan="6">Production Subtotal</td><td class="r mono">{{ fmtCur(calc.group_totals.production) }}</td></tr>
              <tr class="oc-tot"><td colspan="6"><strong>TOTAL COST</strong></td><td class="r mono">{{ fmtCur(calc.group_totals.grand) }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Pricing -->
      <div v-if="calc.pricing && calc.pricing.sell_total" class="oc-result-card">
        <div class="oc-rc-title">Final Pricing — Qty {{ fmtNum(calc.pricing.item_qty) }}</div>
        <table class="oc-ptbl">
          <thead><tr><th>Item</th><th class="r">Per Unit</th><th class="r">Total</th></tr></thead>
          <tbody>
            <tr><td>Unit Cost</td><td class="r mono">{{ fmtCur(calc.pricing.unit_cost) }}</td><td class="r mono">{{ fmtCur(calc.group_totals.grand) }}</td></tr>
            <tr v-if="form.tax_sscl"><td>SSCL (2.5%)</td><td class="r mono">{{ fmtCur(calc.pricing.sscl/(calc.pricing.item_qty||1)) }}</td><td class="r mono">{{ fmtCur(calc.pricing.sscl) }}</td></tr>
            <tr><td>Quoted ({{ form.profit_margin }}% margin)</td><td class="r mono">{{ fmtCur(calc.pricing.quoted_cost/(calc.pricing.item_qty||1)) }}</td><td class="r mono">{{ fmtCur(calc.pricing.quoted_cost) }}</td></tr>
            <tr v-if="form.tax_vat"><td>VAT (18%)</td><td class="r mono">{{ fmtCur(calc.pricing.vat/(calc.pricing.item_qty||1)) }}</td><td class="r mono">{{ fmtCur(calc.pricing.vat) }}</td></tr>
            <tr class="oc-tot"><td><strong>Selling Price</strong></td><td class="r mono">{{ fmtCur(calc.pricing.sell_unit) }}</td><td class="r mono">{{ fmtCur(calc.pricing.sell_total) }}</td></tr>
          </tbody>
        </table>
        <div class="oc-contrib">Material Contribution: <strong>{{ calc.pricing.mat_contrib ? calc.pricing.mat_contrib.toFixed(1) : '0.0' }}%</strong></div>
        <div class="oc-sell-badge">Unit Sell Price: LKR {{ fmtCur(calc.pricing.sell_unit) }}</div>
      </div>

      <div v-if="calcError" class="oc-error">⚠️ {{ calcError }}</div>

      <div v-if="!calcLoading && !(calc.cost_rows && calc.cost_rows.length) && !calcError" class="oc-empty">
        <div style="font-size:40px;margin-bottom:12px">📊</div>
        <p>Select material and machine,<br>tick any additional cost items,<br>then click <strong>Calculate</strong>.</p>
      </div>

    </div>
  </div>
</div>
		`,
	});

	window.__oc_app__ = app.mount(el);
}

// ─────────────────────────────────────────────────────────────
//  STYLES
// ─────────────────────────────────────────────────────────────
function oc_inject_styles() {
	if (document.getElementById('oc-page-styles')) return;
	var s = document.createElement('style'); s.id = 'oc-page-styles';
	s.textContent = `
.oc-wrap{display:grid;grid-template-columns:440px 1fr;gap:16px;padding:16px;min-height:70vh;font-size:13px}
@media(max-width:960px){.oc-wrap{grid-template-columns:1fr}}
.oc-panel{background:#fff;border:1px solid var(--border-color,#d1d5db);border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.06);align-self:start;overflow:hidden}
.oc-ph{display:flex;align-items:center;gap:8px;padding:10px 16px;background:var(--subtle-fg,#f8fafc);border-bottom:1px solid var(--border-color,#d1d5db)}
.oc-ph-split{justify-content:space-between}
.oc-pt{font-weight:700;font-size:13px;color:var(--text-color,#1f272e)}
.oc-badge{font-size:10px;font-weight:700;background:#2c7be5;color:#fff;border-radius:4px;padding:2px 7px;letter-spacing:.05em}
.oc-pb{padding:14px 16px}
.oc-field{margin-bottom:10px}
.oc-lbl{display:block;font-size:10.5px;font-weight:700;color:var(--text-muted,#6b7280);margin-bottom:3px;text-transform:uppercase;letter-spacing:.04em}
.oc-lbl-blue{color:#2c7be5}
.oc-sublbl{font-size:11px;font-weight:600;color:#6b7280;margin-bottom:3px;display:block}
.oc-inp{width:100%;height:30px;padding:0 9px;border:1px solid var(--border-color,#d1d5db);border-radius:4px;font-size:13px;color:var(--text-color,#1f272e);background:var(--control-bg,#fff);box-sizing:border-box;outline:none;transition:border-color .15s,box-shadow .15s}
.oc-inp:focus{border-color:#2c7be5;box-shadow:0 0 0 2px rgba(44,123,229,.15)}
.oc-sel{-webkit-appearance:none;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%236b7280' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 9px center;padding-right:26px;cursor:pointer}
.oc-2col{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.oc-2col-inner{display:flex;gap:6px}.oc-2col-inner .oc-inp{flex:1}
.oc-3col{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px}
.oc-chk-row{display:flex;gap:16px;margin-top:4px}
.oc-chk-lbl{display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12.5px}
.oc-chk{width:14px;height:14px;accent-color:#2c7be5;cursor:pointer;flex-shrink:0}
.oc-divider{border:none;border-top:1px solid #e5e7eb;margin:12px 0}
.oc-divider-label{font-size:10.5px;font-weight:700;color:#1a3a5c;text-transform:uppercase;letter-spacing:.06em;margin:12px 0 8px;padding-bottom:5px;border-bottom:2px solid #eef2f8}
/* Auto cards (Material + Machine) */
.oc-auto-card{background:#f5f8ff;border:1px solid #d0e2ff;border-radius:6px;padding:10px 12px;margin-bottom:12px}
.oc-auto-card-title{font-size:11px;font-weight:700;color:#1a3a5c;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;display:flex;align-items:center;gap:6px}
.oc-auto-dot{width:8px;height:8px;border-radius:50%;background:#d1d5db;flex-shrink:0}
.oc-auto-dot.on{background:#28a745}
.oc-auto-ok{color:#28a745;font-weight:600;font-size:10px;margin-left:4px}
/* Material link field */
.mlf{position:relative}.mlf-row{display:flex;gap:4px;align-items:center}
.mlf-inp{flex:1}
.mlf-x{background:none;border:none;color:#6b7280;cursor:pointer;font-size:13px;padding:0 4px;border-radius:3px;flex-shrink:0}.mlf-x:hover{color:#dc3545}
.mlf-spin{width:14px;height:14px;border:2px solid #d1d5db;border-top-color:#2c7be5;border-radius:50%;animation:oc-spin .6s linear infinite;display:inline-block;flex-shrink:0}
@keyframes oc-spin{to{transform:rotate(360deg)}}
.mlf-badge{display:flex;align-items:center;gap:6px;margin-top:4px;font-size:11.5px}
.mlf-badge-check{color:#28a745;font-weight:700}
.mlf-badge-name{font-weight:600;color:#1f272e}
.mlf-badge-code{color:#6b7280;background:#f0f0f0;border-radius:3px;padding:1px 5px;font-size:10.5px}
.mlf-drop{position:absolute;top:calc(100% + 2px);left:0;right:0;z-index:9999;background:#fff;border:1px solid #d1d5db;border-radius:5px;box-shadow:0 4px 18px rgba(0,0,0,.12);max-height:220px;overflow-y:auto}
.mlf-row-item{display:flex;flex-direction:column;gap:2px;padding:8px 12px;cursor:pointer;border-bottom:1px solid #f5f5f5;transition:background .1s}
.mlf-row-item:last-child{border-bottom:none}
.mlf-row-item:hover,.mlf-row-item.active{background:#e8f0fd}
.mlf-ri-name{font-weight:600;font-size:13px;color:#1f272e}
.mlf-ri-meta{display:flex;align-items:center;gap:8px}
.mlf-ri-code{color:#6b7280;font-size:11px;background:#f0f0f0;border-radius:3px;padding:1px 5px}
.mlf-ri-rate{color:#28a745;font-size:11px;font-weight:600}
.mlf-empty{padding:12px;color:#9ca3af;font-size:12px;font-style:italic;text-align:center}
/* Spec list */
.oc-loading{color:#6b7280;font-style:italic;font-size:12px;padding:8px 0}
.oc-group{margin-bottom:14px}
.oc-group-label{font-size:10px;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;margin-bottom:5px}
.oc-spec{border-radius:5px;margin-bottom:3px;overflow:hidden;border:1px solid transparent}
.oc-spec:has(.oc-spec-label.active){border-color:#2c7be5}
.oc-spec-label{display:flex;align-items:center;gap:8px;padding:7px 10px;cursor:pointer;border-radius:5px;background:#f8fafc;transition:background .12s;user-select:none}
.oc-spec-label.active{background:#e8f0fd}
.oc-spec-label:hover{background:#f0f4ff}
.oc-spec-name{flex:1;font-weight:600;font-size:13px;color:#1f272e}
.oc-spec-detail{padding:10px 12px;background:#f9fbff;border-top:1px solid #e0eaff}
.oc-cf{margin-bottom:8px}.oc-cf:last-child{margin-bottom:0}
.oc-cf-title{font-size:11px;font-weight:700;color:#1a3a5c;margin-bottom:5px;text-transform:uppercase;letter-spacing:.03em}
.oc-cf-auto{display:flex;align-items:center;gap:8px;font-size:12.5px;padding:2px 0}
.oc-cf-item{font-weight:600;color:#1f272e}
.oc-cf-rate{color:#28a745;font-size:11px;font-weight:600}
.oc-cf-info{font-size:11.5px;color:#6b7280;font-style:italic;padding:3px 0}
.oc-attrs{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;padding:8px 10px;background:#f0f4ff;border-radius:4px;border-left:3px solid #2c7be5}
.oc-attr-field{display:flex;flex-direction:column;min-width:80px;flex:1}
.oc-attr-lbl{font-size:11px;font-weight:600;color:#6b7280;margin-bottom:3px}
/* Buttons */
.oc-btn{display:inline-flex;align-items:center;gap:5px;height:28px;padding:0 14px;border:none;border-radius:4px;font-size:12.5px;font-weight:500;cursor:pointer;transition:filter .15s}
.oc-btn:disabled{opacity:.4;cursor:not-allowed}
.oc-btn-blue{background:#2c7be5;color:#fff}.oc-btn-blue:hover:not(:disabled){filter:brightness(1.1)}
.oc-btn-green{background:#28a745;color:#fff}.oc-btn-green:hover:not(:disabled){filter:brightness(1.1)}
.oc-btn-back{background:#6c757d;color:#fff}.oc-btn-back:hover{filter:brightness(1.1)}
.oc-type-toggle{display:flex;gap:2px;background:#e9ecef;border-radius:5px;padding:2px}
.oc-type-btn{background:none;border:none;padding:3px 12px;border-radius:4px;font-size:11px;font-weight:700;cursor:pointer;color:#6b7280;transition:all .15s;letter-spacing:.05em}
.oc-type-btn.active{background:#2c7be5;color:#fff;box-shadow:0 1px 3px rgba(44,123,229,.3)}
.oc-spin{width:14px;height:14px;border:2px solid rgba(0,0,0,.12);border-top-color:#2c7be5;border-radius:50%;animation:oc-spin .6s linear infinite;display:inline-block}
/* Right panel */
.oc-saved-banner{background:#f0fff4;border:1px solid #9ae6b4;border-radius:5px;padding:9px 14px;color:#276749;font-size:13px;display:flex;align-items:center;gap:8px;margin-bottom:14px}
.oc-link-btn{background:none;border:none;color:#2c7be5;cursor:pointer;font-size:13px;text-decoration:underline;margin-left:4px}
.oc-result-card{background:var(--subtle-fg,#f8fafc);border:1px solid var(--border-color,#e5e7eb);border-radius:5px;padding:12px 14px;margin-bottom:14px}
.oc-rc-title{font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#1a3a5c;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #e5e7eb}
.oc-kv-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px}
.oc-kv{display:flex;justify-content:space-between;font-size:12px;padding:4px 8px;border-radius:3px;background:#fff;border:1px solid #eee}
.oc-kv .k{color:#6b7280}.oc-kv .v{font-weight:600;color:#1f272e}.oc-v-blue{color:#2c7be5!important}
.oc-tbl-scroll{overflow-x:auto;border-radius:4px;border:1px solid #d1d5db}
.oc-tbl{width:100%;border-collapse:collapse;font-size:12px}
.oc-tbl thead tr{background:#1a3a5c}
.oc-tbl th{padding:7px 10px;color:#fff;font-weight:600;font-size:11px;letter-spacing:.04em;text-align:left;white-space:nowrap}
.oc-tbl th.r{text-align:right}
.oc-tbl td{padding:6px 10px;border-bottom:1px solid #f0f0f0}
.oc-tbl tbody tr:hover td{background:#f5f8ff}
.oc-auto-row td{background:#fafff7!important}
.oc-auto-tag{display:inline-block;font-size:9px;font-weight:700;background:#28a745;color:#fff;border-radius:3px;padding:1px 4px;margin-left:6px;text-transform:uppercase}
.oc-grp{color:#6b7280;font-size:11px}
.oc-tbl .r,.oc-ptbl .r{text-align:right}
.mono{font-family:monospace;font-weight:600}
.oc-code-badge{display:inline-block;font-size:10px;color:#6b7280;background:#f0f0f0;border-radius:3px;padding:0px 4px;margin-left:5px;font-family:monospace}
.oc-sub td{background:#eef2f8!important;font-weight:600;border-top:1px solid #c8d5e8}
.oc-tot td{background:#1a3a5c!important;color:#fff!important;font-weight:700}
.oc-ptbl{width:100%;border-collapse:collapse;font-size:12px}
.oc-ptbl thead tr{background:#28a745}
.oc-ptbl th{padding:7px 10px;color:#fff;font-weight:600;font-size:11px;letter-spacing:.04em;white-space:nowrap;text-align:left}
.oc-ptbl td{padding:6px 10px;border-bottom:1px solid #f0f0f0}
.oc-ptbl tbody tr:nth-child(even) td{background:#f8fafc}
.oc-contrib{font-size:12px;color:#6b7280;margin-top:10px}
.oc-sell-badge{display:inline-block;background:#28a745;color:#fff;border-radius:4px;padding:5px 16px;font-size:14px;font-weight:700;margin-top:10px}
.oc-error{background:#fff5f5;border:1px solid #fed7d7;border-radius:5px;padding:12px 14px;color:#c53030;font-size:13px;margin-bottom:14px}
.oc-empty{text-align:center;padding:50px 20px;color:#9ca3af}
.oc-empty p{font-size:13px;line-height:1.7;margin-top:8px}
	`;
	document.head.appendChild(s);
}