// Copyright (c) 2026, Techincglobal.com and contributors
// Offset Calculator — Frappe 15 Desk Page
// Route: /app/offset-calculator

frappe.pages['offset-calculator'].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: '',
		single_column: true,
	});
	// Hide the standard page-head to give the calculator full viewport height
	$(wrapper).addClass('oc-page-wrapper');
	$(wrapper).find('.page-head').hide();
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
		getInquiryBreakdowns: 'nxtgen_savinda_pricing_calculator.api.offset_calculator.get_inquiry_breakdowns',
		getOffsetInks: 'nxtgen_savinda_pricing_calculator.api.offset_calculator.get_offset_inks',
		getFlexoFoils: 'nxtgen_savinda_pricing_calculator.api.offset_calculator.get_flexo_foils',
	};

	function debounce(fn, ms) {
		var t;
		return function () { var a = arguments, ctx = this; clearTimeout(t); t = setTimeout(function () { fn.apply(ctx, a); }, ms); };
	}
	function fmtCur(v) {
		return parseFloat(v || 0).toLocaleString('en-LK', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
	}
	function fmtNum(v) { return Math.round(v || 0).toLocaleString('en-LK'); }
	function fmtRate(v) {
		var n = parseFloat(v || 0);
		if (n === 0) return '0.00';
		if (Math.abs(n) >= 1) return n.toLocaleString('en-LK', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
		// Small values like 0.0024: show up to 6 significant figures
		var s = n.toPrecision(6);
		return parseFloat(s).toString();
	}
	function fmtQty(v) {
		var n = parseFloat(v || 0);
		if (n === 0) return '0';
		if (Math.abs(n) >= 1) return n.toLocaleString('en-LK', { minimumFractionDigits: 0, maximumFractionDigits: 4 });
		return n.toPrecision(6);
	}

	var app = Vue.createApp({

		data() {
			return {
				loading: true,
				calcLoading: false,
				saveLoading: false,
				calcError: '',
				savedDocName: '',
				costSheetRef: '',  // Cost Sheet to return to when clicking Back
				viewOnly: false,   // Set to true via ?view_only=1 — disables editing
				sheetExpanded: true,  // Offset: Sheet Requirements collapsible
				flexoExpanded: true,  // Flexo: Reel Requirements collapsible

				allSpecs: [],
				allMachines: [],

				// Selected machine object (not just name)
				selectedMachine: null,
				// machineSpecState[cost_fact] = { selected_item, attr_values, rate, req_qty }
				machineSpecState: {},

				// Flexo: top-level printing machine selection
				flexoPrintMachine: '',

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
					material_type: 'Existing', base_material: '', custom_material_name: '', material_rate: 0,
					no_of_colors: 4, item_qty: 1000,
					profit_margin: 15, extra_prod_cost_pct: 0, tax_sscl: false, tax_vat: false,
					// Offset-specific
					full_sheet_l: 0, full_sheet_w: 0,
					cut_sheet_l: 0, cut_sheet_w: 0,
					no_of_cuts: 2, no_of_ups: 4,
					// Flexo-specific
					reel_width_mm: 0,
					reel_length_m: 0,
					product_width_mm: 0, product_length_mm: 0,
					product_margin_mm: 4, product_gap_mm: 3,
					plate_price: 0,
					plate_count: 0, plate_count_manual: 0,
				},

				calc: { sheet: null, cost_rows: [], group_totals: {}, pricing: null },
				additionalBreakdowns: [],
				newBreakdownQty: '',
				manualCosts: [],   // ad-hoc user cost lines: {name, qty, rate, cost_group}
				collapsedGroups: {},
				collapsedSpecs: {},

				// Production Assignment dialog
				allInks: [],
				allFoils: [],
				inkDialog: null,
				inkDialogState: { machine: '', cycles: 1, csc: false, inks: [], foils: [], manual_process: false, manual_unit: 'Fixed Amount', manual_unit_cost: 0 },
				inkNewInk: '', inkNewPct: 100,
				foilNewName: '', foilNewGroup: 'COLD', foilNewPct: 100,
			};
		},

		computed: {
			selectedSpecs() {
				var n = this.selectedSpecNames;
				return this.allSpecs.filter(function (s) { return n.indexOf(s.spec_name) > -1; });
			},
			specsByGroup() {
				var g = {};
				// Sort: Print first within each group, then alphabetically
				var sorted = this.allSpecs.slice().sort(function (a, b) {
					if (a.spec_name === 'Print') return -1;
					if (b.spec_name === 'Print') return 1;
					return a.spec_name < b.spec_name ? -1 : a.spec_name > b.spec_name ? 1 : 0;
				});
				sorted.forEach(function (s) {
					var gr = s.group || 'Other';
					if (!g[gr]) g[gr] = [];
					g[gr].push(s);
				});
				return g;
			},
			isOffset() { return this.form.pricing_type === 'Offset'; },
			isFlexo() { return this.form.pricing_type === 'Flexo'; },
			totalOrderQty() {
				// form.item_qty is always the canonical total; additionalBreakdowns are the splits for sheet calc only
				return parseFloat(this.form.item_qty) || 0;
			},
			hasBreakdowns() { return this.additionalBreakdowns.length > 0; },
			// Colors added by selected specs (e.g. Gold Color = +1)
			addedColors() {
				return this.selectedSpecs.reduce(function (s, sp) { return s + (parseInt(sp.adds_colors) || 0); }, 0);
			},
			// Effective colors = base input + colors added by selected specs
			effectiveColors() {
				return (parseInt(this.form.no_of_colors) || 0) + this.addedColors;
			},
			// Plate count: manual value if the user set one, else auto = effective colors
			plateCount() {
				return this.form.plate_count_manual ? (parseInt(this.form.plate_count) || 0) : this.effectiveColors;
			},
			// Cost rows grouped into sections (Preparation / Material / Production) with subtotals
			groupedCostRows() {
				var rows = (this.calc && this.calc.cost_rows) || [];
				var order = ['Preparation', 'Material', 'Production'];
				var groups = {};
				rows.forEach(function (r) {
					var g = r.cost_group || 'Other';
					if (!groups[g]) groups[g] = { group: g, rows: [], subtotal: 0 };
					groups[g].rows.push(r);
					groups[g].subtotal += parseFloat(r.amount) || 0;
				});
				var out = [];
				order.forEach(function (g) { if (groups[g]) { out.push(groups[g]); delete groups[g]; } });
				Object.keys(groups).forEach(function (g) { out.push(groups[g]); });
				return out;
			},
			isFlexoDialog() { return this.isFlexo; },
			isDialogMachinePrinting() {
				if (!this.inkDialog || !this.inkDialogState.machine) return false;
				var mList = this.inkDialog.machines || [];
				var mData = mList.find(function (m) { return m.machine === this.inkDialogState.machine; }, this);
				return mData ? !!mData.is_printing_machine : false;
			},
			isDialogMachineInkAllowed() {
				if (!this.inkDialog || !this.inkDialogState.machine) return false;
				var mList = this.inkDialog.machines || [];
				var mData = mList.find(function (m) { return m.machine === this.inkDialogState.machine; }, this);
				return mData ? !!mData.allow_ink_assignment : false;
			},
			// Filtered ink list for the current dialog spec
			dialogInkList() {
				var allowed = this.inkDialog && this.inkDialog.allowed_inks;
				if (allowed && allowed.length) {
					var self = this;
					return self.allInks.filter(function (ink) { return allowed.indexOf(ink.ink_name) !== -1; });
				}
				return this.allInks;
			},

			// Auto-include material row check
			materialReady() {
				var matOk = this.form.material_type === 'Custom'
					? !!(this.form.custom_material_name && this.form.custom_material_name.trim())
					: !!(this.form.base_material);
				return !!(matOk && this.form.material_rate && this.form.item_qty);
			},
			isCustomMaterial() { return this.form.material_type === 'Custom'; },

			// Flexo reel requirements card — show when flexo calc done
			flexoReady() {
				return this.isFlexo && this.calc.sheet && this.calc.sheet.reel_area > 0;
			},

			// Flexo: the spec that has printing machines (is_printing_machine=1)
			flexoPrintingSpec() {
				return this.allSpecs.find(function (s) {
					return s.has_machine && (s.machines || []).some(function (m) { return m.is_printing_machine; });
				}) || null;
			},
			// Flexo: list of printing machines from that spec
			flexoPrintingMachines() {
				var spec = this.flexoPrintingSpec;
				if (!spec) return [];
				return (spec.machines || []).filter(function (m) { return m.is_printing_machine; });
			},
			// Flexo: info object of the currently selected printing machine
			selectedFlexoPrintMachineInfo() {
				var self = this;
				return this.flexoPrintingMachines.find(function (m) { return m.machine === self.flexoPrintMachine; }) || null;
			},

			calcPayload() {
				var self = this;
				var specs = this.selectedSpecs.map(function (spec) {
					var st = self.specState[spec.spec_name] || {};
					var facts = (spec.cost_facts || []).filter(function (cf) {
						// Manual-select cost facts are only included when the user ticks them.
						// Non-manual cost facts are always auto-included (existing behaviour).
						if (!cf.manual_select) return true;
						var s = st[cf.cost_fact] || {};
						return !!s._included;
					}).map(function (cf) {
						var cfst = st[cf.cost_fact] || {};
						var hasItems = cf.master && cf.master.items && cf.master.items.length > 0;
						var rate = parseFloat(cfst.rate || 0);
						if (!rate && !hasItems) rate = parseFloat(self.form.material_rate || 0);
						return {
							cost_fact: cf.cost_fact, is_primary: cf.is_primary,
							selected_item: cfst.selected_item || '',
							attribute_values: cfst.attr_values || {},
							rate: rate, req_qty: parseFloat(cfst.req_qty || 0),
						};
					});
					return {
						spec_name: spec.spec_name,
						has_machine: spec.has_machine || 0,
						adds_colors: spec.adds_colors || 0,
						units: spec.units || 'Full sheet',
						skip_machine_if_spec: spec.skip_machine_if_spec || '',
						machines: spec.machines || [],
						machine_assignment: {
							machine:                st._machine    || '',
							cycles:                 parseInt(st._cycles || 1),
							customer_sample_colors: !!(st._csc),
							inks:                   st._inks   || [],
							foils:                  st._foils  || [],
							manual_process:         !!(st._manual_process),
							manual_unit:            st._manual_unit      || 'Fixed Amount',
							manual_unit_cost:       parseFloat(st._manual_unit_cost || 0),
							skip_machine:           self.effectiveSkip(spec.spec_name),
						},
						cost_facts: facts,
					};
				});

				// Machine spec — same structure as a regular spec
				var machine_spec = null;
				if (this.selectedMachine) {
					var mfacts = (this.selectedMachine.cost_facts || []).map(function (cf) {
						var mst = self.machineSpecState[cf.cost_fact] || {};
						return {
							cost_fact: cf.cost_fact, is_primary: cf.is_primary,
							selected_item: mst.selected_item || '',
							attribute_values: mst.attr_values || {},
							rate: parseFloat(mst.rate || 0),
							req_qty: parseFloat(mst.req_qty || 0),
						};
					});
					machine_spec = {
						spec_name: this.selectedMachine.spec_name,
						group: 'Machine',
						cost_facts: mfacts,
					};
				}
				var formData = Object.assign({}, this.form);
				// breakdown_qtys = individual split quantities (sheet calc only); item_qty = total (all formulas)
				formData.breakdown_qtys = this.additionalBreakdowns.length > 0
					? this.additionalBreakdowns.map(function (q) { return parseFloat(q) || 0; })
					: [];
				// Manual ad-hoc cost lines (only valid rows: have a name and a non-zero amount)
				formData.manual_costs = (this.manualCosts || [])
					.map(function (m) {
						return {
							name: (m.name || '').trim(),
							qty: parseFloat(m.qty) || 0,
							rate: parseFloat(m.rate) || 0,
							cost_group: m.cost_group || 'Production',
						};
					})
					.filter(function (m) { return m.name && m.qty * m.rate; });
				return { form: formData, selected_specs: specs, machine_spec: machine_spec };
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
				this.autoSelectOperations = ops.split('|').filter(Boolean);
			}
			if (urlParams.get('view_only') === '1') this.viewOnly = true;
			// Read bqtys — breakdown split quantities passed from Cost Sheet (affect sheet calc only)
			var bqtys = urlParams.get('bqtys');
			if (bqtys) {
				var qtys = bqtys.split(',').map(function (q) { return parseFloat(q) || 0; }).filter(function (q) { return q > 0; });
				if (qtys.length > 0) {
					this.additionalBreakdowns = qtys;
					// Total order qty = sum of all splits
					this.form.item_qty = qtys.reduce(function (s, q) { return s + q; }, 0);
				}
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
				} else if (!name) {
					// New calculation → auto-select specs flagged "Auto Select" (cascades children)
					this._autoSelectDefaults();
				}
			},

			loadData() {
				var self = this;
				var pt = self.form.pricing_type || 'Offset';
				var done = 0;
				function check() { done++; if (done >= 3) { self.loading = false; self.checkUrlRef(); } }
				frappe.call({ method: API.getSpecs, args: { pricing_type: pt }, callback: function (r) {
					self.allSpecs = r.message || [];
					if (self.needsAutoSelect) { self.needsAutoSelect = false; self._autoSelectFromOperations(); }
					check();
				}});
				frappe.call({ method: API.getMachines, args: { pricing_type: pt }, callback: function (r) { self.allMachines = r.message || []; check(); } });
				// Load inks + foils (needed for both Offset and Flexo Production Assignment)
				var inkDone = 0;
				function checkInkFoil() { inkDone++; if (inkDone >= 2) check(); }
				if (!self.allInks.length) {
					frappe.call({ method: API.getOffsetInks, callback: function (r) { self.allInks = r.message || []; checkInkFoil(); } });
				} else { checkInkFoil(); }
				if (!self.allFoils.length) {
					frappe.call({ method: API.getFlexoFoils, callback: function (r) { self.allFoils = r.message || []; checkInkFoil(); } });
				} else { checkInkFoil(); }
			},

			// Reload specs when pricing type changes
			onPricingTypeChange() {
				var self = this;
				self.loading = true;
				self.selectedSpecNames = [];
				self.specState = {};
				self.selectedMachine = null;
				self.machineSpecState = {};
				self.flexoPrintMachine = '';
				self.calc = { sheet: null, cost_rows: [], group_totals: {}, pricing: null };
				var pt = self.form.pricing_type || 'Offset';
				var done = 0;
				function check() { done++; if (done >= 2) { self.loading = false; } }
				frappe.call({ method: API.getSpecs, args: { pricing_type: pt }, callback: function (r) { self.allSpecs = r.message || []; self._autoSelectDefaults(); check(); } });
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
						self.machineSpecState[cf.cost_fact] = { selected_item: sel, attr_values: self._defaultAttrValues(cf), rate: rate, req_qty: 0 };
					});
				}
				this.scheduleCalc();
			},

			// ── Flexo printing machine (top-level card) ──
			onFlexoPrintMachineChange(machineName) {
				var self = this;
				this.flexoPrintMachine = machineName;
				var spec = this.flexoPrintingSpec;
				if (!spec) { this.scheduleCalc(); return; }
				var specName = spec.spec_name;
				// Auto-select the printing spec when a machine is chosen
				if (machineName && this.selectedSpecNames.indexOf(specName) === -1) {
					this.selectedSpecNames.push(specName);
					this.initSpecState(specName);
				}
				// Assign machine into spec state
				if (this.specState[specName]) {
					this.specState[specName]._machine = machineName || '';
				}
				this.scheduleCalc();
			},

			// ── Specs (checkboxes) ──
			toggleSpec(specName) {
				if (this.isSelected(specName)) {
					this._deselectSpec(specName);
				} else {
					this._selectSpec(specName);
				}
				this.scheduleCalc();
			},
			// Select a spec and cascade-select ALL its children (recursively)
			_selectSpec(specName) {
				var self = this;
				if (self.selectedSpecNames.indexOf(specName) === -1) {
					self.selectedSpecNames.push(specName);
					self.initSpecState(specName);
				}
				self.allSpecs.forEach(function (s) {
					if (s.parent_spec === specName && self.selectedSpecNames.indexOf(s.spec_name) === -1) {
						self._selectSpec(s.spec_name);
					}
				});
			},
			// Deselect a spec and cascade-deselect its children
			_deselectSpec(specName) {
				var self = this;
				var i = self.selectedSpecNames.indexOf(specName);
				if (i > -1) self.selectedSpecNames.splice(i, 1);
				delete self.specState[specName];
				if (self.isFlexo && self.flexoPrintingSpec && self.flexoPrintingSpec.spec_name === specName) {
					self.flexoPrintMachine = '';
				}
				self.allSpecs.forEach(function (s) {
					if (s.parent_spec === specName) self._deselectSpec(s.spec_name);
				});
			},
			isSelected(n) { return this.selectedSpecNames.indexOf(n) > -1; },

			// Depth of a spec in the parent chain (for indentation)
			specDepth(specName) {
				var self = this, d = 0, cur = specName, guard = 0;
				while (guard++ < 20) {
					var s = self.allSpecs.find(function (x) { return x.spec_name === cur; });
					if (!s || !s.parent_spec) break;
					d++; cur = s.parent_spec;
				}
				return d;
			},
			// Ordered, tree-aware list of specs in a group: top-level first; a spec's
			// children appear right under it ONLY when the spec is selected.
			groupSpecTreeSpecs(specs) {
				var self = this;
				var names = {};
				specs.forEach(function (s) { names[s.spec_name] = true; });
				var byParent = {};
				specs.forEach(function (s) {
					var p = (s.parent_spec && names[s.parent_spec]) ? s.parent_spec : '';
					(byParent[p] = byParent[p] || []).push(s);
				});
				var out = [];
				function walk(parent) {
					(byParent[parent] || []).forEach(function (s) {
						out.push(s);
						if (self.isSelected(s.spec_name) && byParent[s.spec_name]) walk(s.spec_name);
					});
				}
				walk('');
				return out;
			},
			// Auto-select specs flagged Auto Select (top-level → cascades children)
			_autoSelectDefaults() {
				var self = this;
				this.allSpecs.forEach(function (s) {
					if ((parseInt(s.auto_select) || 0) && !s.parent_spec && self.selectedSpecNames.indexOf(s.spec_name) === -1) {
						self._selectSpec(s.spec_name);
					}
				});
			},

			initSpecState(specName) {
				var self = this;
				var spec = this.allSpecs.find(function (s) { return s.spec_name === specName; });
				if (!spec) return;
				var state = {};
				(spec.cost_facts || []).forEach(function (cf) {
					var items = (cf.master && cf.master.items) || [];
					var sel = items.length === 1 ? items[0].item : '';
					var rate = (items.length === 1 && items[0].is_fix_rate) ? (items[0].rate || 0) : 0;
					state[cf.cost_fact] = { selected_item: sel, attr_values: self._defaultAttrValues(cf), rate: rate, req_qty: 0 };
					if (sel && !(items[0] && items[0].is_fix_rate)) self.fetchItemRate(specName, cf.cost_fact, sel);
				});
				// Machine state fields
				if (spec.has_machine) {
					state._machine = '';
					state._cycles = 1;
					state._csc = false;
					state._inks = [];
					state._foils = [];
				}
				this.specState[specName] = state;
			},

			// Helper: check if the currently selected machine for a spec is a printing machine
			isPrintingMachine(specName) {
				var st = this.specState[specName] || {};
				var machineName = st._machine;
				if (!machineName) return false;
				var spec = this.allSpecs.find(function (s) { return s.spec_name === specName; });
				if (!spec) return false;
				var mData = (spec.machines || []).find(function (m) { return m.machine === machineName; });
				return !!(mData && mData.is_printing_machine);
			},

			// Auto-select Finishing specs whose operation matches the inquiry's operations list.
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
				var isNumeric = attrType === 'Number' || attrType === 'Float' || attrType === 'Percentage';
				st.attr_values[attrName] = isNumeric ? (parseFloat(value) || 0) : value;
				this.scheduleCalc();
			},

			onMachineAttrChange(costFact, attrName, value, attrType) {
				var st = this.machineSpecState[costFact];
				if (!st) return;
				if (!st.attr_values) st.attr_values = {};
				var isNumeric = attrType === 'Number' || attrType === 'Float' || attrType === 'Percentage';
				st.attr_values[attrName] = isNumeric ? (parseFloat(value) || 0) : value;
				this.scheduleCalc();
			},

			getAttrValue(sn, cf, a) { return ((this.specState[sn] || {})[cf] || {}).attr_values && ((this.specState[sn] || {})[cf] || {}).attr_values[a] || ''; },
			getMachineAttrValue(cf, a) { return (this.machineSpecState[cf] || {}).attr_values && (this.machineSpecState[cf] || {}).attr_values[a] || ''; },

				openInkDialog(spec) {
				var st = this.specState[spec.spec_name] || {};
				this.inkDialog = spec;
				this.inkDialogState = {
					machine:      st._machine      || '',
					cycles:       parseInt(st._cycles || 1),
					csc:          !!(st._csc),
					inks:         JSON.parse(JSON.stringify(st._inks  || [])),
					foils:        JSON.parse(JSON.stringify(st._foils || [])),
					manual_process:   !!(st._manual_process),
					manual_unit:      st._manual_unit      || 'Fixed Amount',
					manual_unit_cost: parseFloat(st._manual_unit_cost || 0),
				};
				this.inkNewInk = '';
				this.inkNewPct = 100;
				this.foilNewName = '';
				this.foilNewGroup = 'COLD';
				this.foilNewPct = 100;
			},
			saveInkDialog() {
				if (!this.inkDialog) return;
				var st = this.specState[this.inkDialog.spec_name];
				if (!st) return;
				st._machine          = this.inkDialogState.machine;
				st._cycles           = this.inkDialogState.cycles;
				st._csc              = this.inkDialogState.csc;
				st._inks             = JSON.parse(JSON.stringify(this.inkDialogState.inks));
				st._foils            = JSON.parse(JSON.stringify(this.inkDialogState.foils));
				st._manual_process   = this.inkDialogState.manual_process;
				st._manual_unit      = this.inkDialogState.manual_unit;
				st._manual_unit_cost = this.inkDialogState.manual_unit_cost;
				// Sync Flexo top-level machine card if this is the printing spec
				if (this.isFlexo && this.flexoPrintingSpec && this.flexoPrintingSpec.spec_name === this.inkDialog.spec_name) {
					this.flexoPrintMachine = this.inkDialogState.machine || '';
				}
				this.inkDialog = null;
				this.scheduleCalc();
			},
			addDialogInk() {
				var name = this.inkNewInk;
				var pct  = parseFloat(this.inkNewPct) || 100;
				if (!name) return;
				this.inkDialogState.inks.push({ ink_name: name, percentage: pct });
				this.inkNewInk = '';
				this.inkNewPct = 100;
			},
			removeDialogInk(idx) {
				this.inkDialogState.inks.splice(idx, 1);
			},
			addDialogFoil() {
				var name  = this.foilNewName;
				var grp   = this.foilNewGroup || 'COLD';
				var pct   = parseFloat(this.foilNewPct) || 100;
				if (!name) return;
				// Get foil_name from the selected foil key (which is foil.name = "HOT-Gold" etc.)
				var foilDoc = this.allFoils.find(function(f){ return f.name === name; });
				this.inkDialogState.foils.push({
					foil_name:  foilDoc ? foilDoc.foil_name : name,
					foil_key:   name,
					foil_group: foilDoc ? foilDoc.foil_group : grp,
					percentage: pct,
				});
				this.foilNewName = '';
				this.foilNewPct  = 100;
			},
			removeDialogFoil(idx) {
				this.inkDialogState.foils.splice(idx, 1);
			},
			toggleGroup(name) {
				this.collapsedGroups[name] = !this.collapsedGroups[name];
			},
			toggleSpecCollapse(name) {
				this.collapsedSpecs[name] = !this.collapsedSpecs[name];
			},
			addBreakdown() {
				var q = parseFloat(this.newBreakdownQty);
				if (!q || q <= 0) return;
				this.additionalBreakdowns.push(q);
				this.newBreakdownQty = '';
				this.scheduleCalc();
			},
			removeBreakdown(idx) {
				this.additionalBreakdowns.splice(idx, 1);
				this.scheduleCalc();
			},
			// Build the initial attribute values for a cost fact from its attribute defaults
			_defaultAttrValues(cf) {
				var out = {};
				var attrs = (cf.master && cf.master.attributes) || [];
				attrs.forEach(function (a) {
					if (a.default_value === null || a.default_value === undefined || a.default_value === '') return;
					var t = a.type;
					out[a.attribute_name] = (t === 'Number' || t === 'Float' || t === 'Percentage')
						? (parseFloat(a.default_value) || 0)
						: a.default_value;
				});
				return out;
			},
			// ── Manual-select cost facts inside a spec ──
			isCostFactIncluded(specName, cf) {
				if (!cf.manual_select) return true;   // auto cost fact
				var st = this.specState[specName] || {};
				var cfst = st[cf.cost_fact] || {};
				return !!cfst._included;
			},
			toggleCostFact(specName, cfName) {
				if (!this.specState[specName]) this.specState[specName] = {};
				if (!this.specState[specName][cfName]) this.specState[specName][cfName] = {};
				var cfst = this.specState[specName][cfName];
				cfst._included = !cfst._included;
				// Vue 2/3 reactivity for freshly-added nested keys
				this.specState[specName][cfName] = Object.assign({}, cfst);
				this.scheduleCalc();
			},

			// ── Machine-skip rules on a spec ──
			anyPrintingActive() {
				var self = this;
				if (this.flexoPrintMachine) return true;
				return this.selectedSpecs.some(function (s) { return self.isPrintingMachine(s.spec_name); });
			},
			specHasSkipRule(spec) {
				return !!(spec.has_machine && (spec.skip_machine_if_spec || spec.skip_machine_if_printing || spec.skip_machine_if_machine));
			},
			// Auto-skip decision from the spec's configured rules + current selections
			specAutoSkip(specName) {
				var spec = this.allSpecs.find(function (s) { return s.spec_name === specName; });
				if (!spec || !spec.has_machine) return false;
				// (a) legacy: skip if a named spec is also selected
				if (spec.skip_machine_if_spec && this.selectedSpecNames.indexOf(spec.skip_machine_if_spec) > -1) return true;
				// (b) skip if any printing machine is active
				if (spec.skip_machine_if_printing && this.anyPrintingActive()) return true;
				// (c) a linked machine is configured → default to skipped (the machine
				// cost is assumed covered by the linked machine). Overridable — untick
				// to charge this spec's machine anyway.
				if (spec.skip_machine_if_machine) return true;
				return false;
			},
			// Effective skip = user override if set, else the auto decision
			effectiveSkip(specName) {
				var st = this.specState[specName] || {};
				if (st._skipOverride === true || st._skipOverride === false) return st._skipOverride;
				return this.specAutoSkip(specName);
			},
			toggleSkipMachine(specName) {
				if (!this.specState[specName]) this.specState[specName] = {};
				this.specState[specName]._skipOverride = !this.effectiveSkip(specName);
				this.specState[specName] = Object.assign({}, this.specState[specName]);
				this.scheduleCalc();
			},
			// Wording for the skip-machine checkbox. When the machine has ink assigned,
			// skipping removes only the machine (hourly) cost — the ink is still charged.
			skipMachineLabel(specName) {
				var st = this.specState[specName] || {};
				var hasInk = (st._inks || []).length > 0;
				if (this.effectiveSkip(specName)) {
					return hasInk
						? 'Machine cost skipped — ink still charged'
						: 'Machine cost skipped (inline / covered elsewhere)';
				}
				return hasInk
					? 'Charging machine + ink cost — tick to skip machine cost only'
					: 'Charging machine cost — tick to skip';
			},
			// Plate count manual override
			onPlateCountInput(val) {
				this.form.plate_count_manual = 1;
				this.form.plate_count = parseInt(val) || 0;
				this.scheduleCalc();
			},
			resetPlateCount() {
				this.form.plate_count_manual = 0;
				this.form.plate_count = 0;
				this.scheduleCalc();
			},
			addManualCost() {
				this.manualCosts.push({ name: '', qty: 1, rate: 0, cost_group: 'Production' });
			},
			removeManualCost(idx) {
				this.manualCosts.splice(idx, 1);
				this.scheduleCalc();
			},
			manualCostAmount(m) {
				return (parseFloat(m.qty) || 0) * (parseFloat(m.rate) || 0);
			},
			fetchInquiryBreakdowns(ref) {
				var self = this;
				if (!ref || !self.isOffset) return;
				frappe.call({
					method: API.getInquiryBreakdowns,
					args: { ref: ref },
					callback: function (r) {
						if (r.message && r.message.length > 0) {
							self.form.item_qty = parseFloat(r.message[0].qty) || self.form.item_qty;
							var allQtys = r.message.map(function (b) { return parseFloat(b.qty) || 0; }).filter(function (q) { return q > 0; });
						self.additionalBreakdowns = allQtys;
						if (allQtys.length > 0) self.form.item_qty = allQtys.reduce(function (s, q) { return s + q; }, 0);
							self.scheduleCalc();
						}
					},
				});
			},

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
							// merge manual cost lines into the saved form so they persist + restore
							form: Object.assign({}, self.form, { manual_costs: self.calcPayload.form.manual_costs }),
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

			printPage() {
				if (!this.savedDocName) {
					frappe.msgprint({ title: 'Save First', message: 'Save the calculation before printing.', indicator: 'orange' });
					return;
				}
				var url = '/printview?doctype=Calculation+Breakdown&name='
					+ encodeURIComponent(this.savedDocName)
					+ '&format=Product+Costing+Summary&no_letterhead=0';
				window.open(frappe.urllib.get_full_url(url));
			},

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
							// Restore manual ad-hoc cost lines
							self.manualCosts = Array.isArray(d.form.manual_costs)
								? d.form.manual_costs.map(function (m) {
									return {
										name: m.name || '',
										qty: parseFloat(m.qty) || 0,
										rate: parseFloat(m.rate) || 0,
										cost_group: m.cost_group || 'Production',
									};
								})
								: [];
							// Restore matSearch display and fetch material rate if not saved in ui_state
							if (d.form.base_material) {
								frappe.call({
									method: 'frappe.client.get_value',
									args: { doctype: 'Item', filters: { name: d.form.base_material }, fieldname: ['item_name', 'valuation_rate'] },
									callback: function (r2) {
										self.matSearch = (r2.message && r2.message.item_name) || d.form.base_material;
										// material_rate is 0 for new CBs (no saved ui_state) — fetch it now
										if (!self.form.material_rate) {
											var valRate = parseFloat((r2.message && r2.message.valuation_rate) || 0);
											if (self.form.price_list) {
												frappe.call({
													method: 'frappe.client.get_value',
													args: { doctype: 'Item Price', filters: { item_code: d.form.base_material, price_list: self.form.price_list, selling: 1 }, fieldname: 'price_list_rate' },
													callback: function (r3) {
														self.form.material_rate = parseFloat((r3.message && r3.message.price_list_rate) || valRate || 0);
														if (self.form.material_rate) self.scheduleCalc();
													},
												});
											} else {
												self.form.material_rate = valRate;
												if (self.form.material_rate) self.scheduleCalc();
											}
										}
									},
								});
							} else if (d.form.material_type === 'Custom' && d.form.custom_material_name && d.form.material_rate) {
								// Custom material with rate already saved — trigger calculation immediately
								self.scheduleCalc();
							}
						}
						// Restore machine
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
						// Restore specs (including machine assignment state)
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
									// Only included cost facts are saved, so restore them as ticked
									// (matters for manual-select cost facts).
									_included: true,
								};
							});
							// Restore machine assignment if this spec has_machine
							if (spec.has_machine && spec.machine_assignment) {
								state._machine = spec.machine_assignment.machine || '';
								state._cycles  = parseInt(spec.machine_assignment.cycles || 1);
								state._csc     = !!(spec.machine_assignment.customer_sample_colors);
								state._inks    = spec.machine_assignment.inks  || [];
								state._foils   = spec.machine_assignment.foils || [];
								// Restore the skip-machine choice if it was saved
								if (typeof spec.machine_assignment.skip_machine === 'boolean') {
									state._skipOverride = spec.machine_assignment.skip_machine;
								}
							} else if (spec.has_machine) {
								state._machine = '';
								state._cycles  = 1;
								state._inks    = [];
								state._foils   = [];
								state._csc     = false;
							}
							self.specState[spec.spec_name] = state;
					});
					// For Flexo: sync top-level print machine from the printing spec's saved assignment
					if (self.isFlexo || (d.form && d.form.pricing_type === 'Flexo')) {
						var printSpec = self.allSpecs.find(function (s) {
							return s.has_machine && (s.machines || []).some(function (m) { return m.is_printing_machine; });
						});
						if (printSpec && self.specState[printSpec.spec_name]) {
							self.flexoPrintMachine = self.specState[printSpec.spec_name]._machine || '';
						}
					}
					// Restore ALL breakdown splits (not just the "additional" ones)
						self.additionalBreakdowns = (d.form && d.form.breakdown_qtys && d.form.breakdown_qtys.length > 0)
							? d.form.breakdown_qtys.map(function (q) { return parseFloat(q) || 0; }).filter(function (q) { return q > 0; })
							: [];
						{  // dummy block to match old else
						}
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
				this.form.base_material = ''; this.form.custom_material_name = ''; this.form.material_rate = 0;
				this.matSearch = ''; this.matResults = []; this.matOpen = false;
				this.scheduleCalc();
			},

			fmtCur, fmtNum, fmtRate, fmtQty,
		},

		watch: {
			'form.ref': function (newRef) {
				// Auto-fetch inquiry breakdowns ONLY in standalone calculator mode.
				// When launched from a Cost Sheet, splits are controlled entirely by the
				// bqtys URL param (group membership): no group → no split, group → split by
				// group members. So don't auto-split by the inquiry's breakdown list here.
				if (newRef && !this.costSheetRef && !this.additionalBreakdowns.length) {
					this.fetchInquiryBreakdowns(newRef);
				}
			},
		},

		// ── TEMPLATE ────────────────────────────────────────────────
		template: `
<div class="oc-wrap">

  <!-- ═══════ PRODUCTION ASSIGNMENT DIALOG ═══════ -->
  <div v-if="inkDialog" class="oc-pa-overlay" @click.self="inkDialog = null">
    <div class="oc-pa-dialog">
      <div class="oc-pa-hdr">Production Assignment: <b>{{ inkDialog.spec_name }}</b></div>
      <div class="oc-pa-body">
        <!-- Manual Process toggle -->
        <div class="oc-field">
          <label class="oc-chk-lbl" style="font-weight:600">
            <input type="checkbox" v-model="inkDialogState.manual_process" class="oc-chk" />
            <span>Manual Process</span>
          </label>
          <div class="oc-hint">When checked, skips machine cost formulas and uses a fixed manual cost instead.</div>
        </div>

        <!-- Machine + Cycles (hidden when Manual Process) -->
        <template v-if="!inkDialogState.manual_process">
        <div class="oc-field">
          <label class="oc-sublbl">Select Machine</label>
          <select v-model="inkDialogState.machine" class="oc-inp oc-sel">
            <option value="">— Select Machine —</option>
            <option v-for="m in inkDialog.machines" :key="m.machine" :value="m.machine">{{ m.machine }}</option>
          </select>
        </div>
        <div class="oc-field">
          <label class="oc-sublbl">Machine Cycles</label>
          <input type="number" v-model.number="inkDialogState.cycles" min="1" class="oc-inp" placeholder="Number of cycles" />
          <div class="oc-hint">Multiplies make-ready and production time.</div>
        </div>
        </template>

        <!-- Manual cost fields (shown when Manual Process) -->
        <template v-if="inkDialogState.manual_process">
        <div class="oc-field">
          <label class="oc-sublbl">Manual Calculation Unit</label>
          <select v-model="inkDialogState.manual_unit" class="oc-inp oc-sel">
            <option value="Per Item">Per Item</option>
            <option value="Per Cut Sheet">Per Cut Sheet</option>
            <option value="Per Full Sheet">Per Full Sheet</option>
            <option value="Fixed Amount">Fixed Amount</option>
          </select>
        </div>
        <div class="oc-field">
          <label class="oc-sublbl">Unit Cost (Rs.)</label>
          <input type="number" v-model.number="inkDialogState.manual_unit_cost" min="0" class="oc-inp" placeholder="0.00" />
        </div>
        </template>

        <!-- Offset: CSC (printing machines only) + Inks (printing OR allow_ink_assignment) -->
        <template v-if="!isFlexoDialog && (isDialogMachinePrinting || isDialogMachineInkAllowed)">
          <div v-if="isDialogMachinePrinting" class="oc-field">
            <label class="oc-chk-lbl">
              <input type="checkbox" v-model="inkDialogState.csc" class="oc-chk" />
              <span>Customer Sample Colors</span>
            </label>
          </div>
          <div class="oc-field">
            <label class="oc-sublbl">Assigned Inks</label>
            <div v-if="!inkDialogState.inks.length" class="oc-no-inks">No inks assigned yet.</div>
            <div v-for="(ink, i) in inkDialogState.inks" :key="i" class="oc-ink-chip">
              <span class="oc-ink-chip-label">{{ ink.ink_name }} <span class="oc-ink-pct">({{ ink.percentage }}%)</span></span>
              <button @click="removeDialogInk(i)" class="oc-ink-rm" title="Remove">🗑</button>
            </div>
          </div>
          <div class="oc-field">
            <label class="oc-sublbl">Add Ink</label>
            <div class="oc-add-ink">
              <select v-model="inkNewInk" class="oc-inp oc-sel oc-ink-sel">
                <option value="">Select Ink</option>
                <option v-for="ink in dialogInkList" :key="ink.ink_name" :value="ink.ink_name">{{ ink.ink_name }}</option>
              </select>
              <input type="number" v-model.number="inkNewPct" min="1" max="100" class="oc-inp oc-pct-inp" />
              <span class="oc-pct-sym">%</span>
              <button @click="addDialogInk" class="oc-btn oc-btn-blue oc-btn-sm">Add</button>
            </div>
          </div>
        </template>

        <!-- Flexo: Foils + Inks -->
        <template v-if="isFlexoDialog">
          <!-- Foils -->
          <div class="oc-pa-section-hdr">Foils</div>
          <div v-if="!inkDialogState.foils.length" class="oc-no-inks">No foils assigned.</div>
          <div v-for="(foil, i) in inkDialogState.foils" :key="i" class="oc-ink-chip"
               :style="foil.foil_group==='COLD' ? 'background:#e3f2fd' : 'background:#fff3e0'">
            <span class="oc-ink-chip-label">
              <span class="oc-badge-csc">{{ foil.foil_group }}</span>
              {{ foil.foil_name }} <span class="oc-ink-pct">({{ foil.percentage }}%)</span>
            </span>
            <button @click="removeDialogFoil(i)" class="oc-ink-rm">🗑</button>
          </div>
          <div class="oc-add-ink" style="margin-top:6px">
            <select v-model="foilNewName" class="oc-inp oc-sel oc-ink-sel">
              <option value="">Select Foil</option>
              <optgroup label="COLD Foil">
                <option v-for="f in allFoils.filter(function(x){return x.foil_group==='COLD'})" :key="f.name" :value="f.name">{{ f.foil_name }}</option>
              </optgroup>
              <optgroup label="HOT Foil">
                <option v-for="f in allFoils.filter(function(x){return x.foil_group==='HOT'})" :key="f.name" :value="f.name">{{ f.foil_name }}</option>
              </optgroup>
            </select>
            <input type="number" v-model.number="foilNewPct" min="1" max="100" class="oc-inp oc-pct-inp" />
            <span class="oc-pct-sym">%</span>
            <button @click="addDialogFoil" class="oc-btn oc-btn-blue oc-btn-sm">Add</button>
          </div>
          <!-- Inks -->
          <div class="oc-pa-section-hdr" style="margin-top:12px">Inks</div>
          <div v-if="!inkDialogState.inks.length" class="oc-no-inks">No inks assigned.</div>
          <div v-for="(ink, i) in inkDialogState.inks" :key="i" class="oc-ink-chip">
            <span class="oc-ink-chip-label">{{ ink.ink_name }}</span>
            <button @click="removeDialogInk(i)" class="oc-ink-rm">🗑</button>
          </div>
          <div class="oc-add-ink" style="margin-top:6px">
            <select v-model="inkNewInk" class="oc-inp oc-sel oc-ink-sel">
              <option value="">Select Ink</option>
              <option v-for="ink in dialogInkList" :key="ink.ink_name" :value="ink.ink_name">{{ ink.ink_name }}</option>
            </select>
            <button @click="addDialogInk" class="oc-btn oc-btn-blue oc-btn-sm">Add</button>
          </div>
        </template>
      </div>
      <div class="oc-pa-ftr">
        <button @click="inkDialog = null" class="oc-btn oc-btn-cancel">Cancel</button>
        <button @click="saveInkDialog" class="oc-btn oc-btn-blue">Assign</button>
      </div>
    </div>
  </div>

  <!-- ═══════ LEFT PANEL ═══════ -->
  <div class="oc-panel" :class="{'oc-view-overlay': viewOnly}">
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
        <!-- Material type toggle -->
        <div class="oc-field">
          <label class="oc-lbl">Material Type</label>
          <div class="oc-type-toggle" style="margin-top:2px">
            <button class="oc-type-btn" :class="{active: !isCustomMaterial}"
              @click="form.material_type='Existing'; scheduleCalc()">Existing Item</button>
            <button class="oc-type-btn" :class="{active: isCustomMaterial}"
              @click="form.material_type='Custom'; scheduleCalc()">Custom / Non-stock</button>
          </div>
        </div>
        <!-- Existing: Item link-field -->
        <div v-if="!isCustomMaterial" class="oc-field">
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
            <div v-if="form.base_material && !matOpen" class="mlf-badge">
              <span class="mlf-badge-check">✓</span>
              <span class="mlf-badge-name">{{ matSearch }}</span>
              <span class="mlf-badge-code">{{ form.base_material }}</span>
            </div>
            <div v-if="matOpen" class="mlf-drop">
              <div v-if="matResults.length===0&&!matLoading" class="mlf-empty">No items found</div>
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
        <!-- Custom: free-text name -->
        <div v-if="isCustomMaterial" class="oc-field">
          <label class="oc-lbl oc-lbl-blue">{{ isFlexo ? 'Reel Material Name' : 'Board / Paper Name' }}</label>
          <input v-model="form.custom_material_name" type="text" class="oc-inp"
            placeholder="e.g. 300gsm Art Board (unregistered)"
            @input="scheduleCalc" />
          <div class="oc-hint" style="margin-top:3px">This name will appear in the cost breakdown and print format.</div>
        </div>
        <div class="oc-field">
          <label class="oc-lbl">Rate (LKR / {{ isFlexo ? 'm²' : 'full sheet' }})</label>
          <input v-model.number="form.material_rate" type="number" min="0" step="0.01" class="oc-inp" @change="scheduleCalc" />
        </div>
      </div>

      <!-- ══ FLEXO: Printing Machine card ══ -->
      <div v-if="isFlexo && flexoPrintingMachines.length" class="oc-auto-card">
        <div class="oc-auto-card-title">
          <span class="oc-auto-dot" :class="flexoPrintMachine ? 'on' : ''"></span>
          Printing Machine
          <span v-if="flexoPrintMachine" class="oc-auto-ok">✓ {{ flexoPrintMachine }}</span>
        </div>
        <div class="oc-field">
          <label class="oc-lbl">Select Printing Machine</label>
          <select :value="flexoPrintMachine" class="oc-inp oc-sel" @change="onFlexoPrintMachineChange($event.target.value)">
            <option value="">— Select Machine —</option>
            <option v-for="m in flexoPrintingMachines" :key="m.machine" :value="m.machine">{{ m.machine }}</option>
          </select>
        </div>
        <div v-if="selectedFlexoPrintMachineInfo" class="oc-hint" style="margin-top:2px">
          LKR {{ (selectedFlexoPrintMachineInfo.machine_cost_per_hour || 0).toLocaleString() }}/hr
          &nbsp;·&nbsp; Capacity: {{ selectedFlexoPrintMachineInfo.color_capacity || '—' }} colors
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
          <div>
            <div class="oc-field">
              <label class="oc-lbl">Order Quantity <span v-if="hasBreakdowns" class="oc-total-badge">Splits: {{ fmtNum(additionalBreakdowns.reduce(function(s,q){return s+(parseFloat(q)||0);},0)) }}</span></label>
              <input v-model.number="form.item_qty" type="number" min="1" class="oc-inp" @change="scheduleCalc" />
            </div>
            <!-- Breakdown splits — only shown when splits are loaded (combined group items) -->
            <div v-if="hasBreakdowns" class="oc-breakdown-wrap">
              <div class="oc-chips-label">Sheet splits:</div>
              <div class="oc-chips-row">
                <span v-for="(q, i) in additionalBreakdowns" :key="i" class="oc-chip-extra">
                  {{ fmtNum(q) }}<button class="oc-chip-x" @click="removeBreakdown(i)" title="Remove">×</button>
                </span>
                <span class="oc-chip-adder">
                  <input type="number" v-model="newBreakdownQty" class="oc-chip-inp" placeholder="+ split" min="1" @keyup.enter="addBreakdown" />
                  <button class="oc-btn-mini" @click="addBreakdown">Add</button>
                </span>
              </div>
            </div>
          </div>
          <div class="oc-field"><label class="oc-lbl">Profit Margin (%)</label><input v-model.number="form.profit_margin" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
          <div class="oc-field"><label class="oc-lbl">Extra Production Cost (%)</label><input v-model.number="form.extra_prod_cost_pct" type="number" min="0" step="0.5" class="oc-inp" @change="scheduleCalc" /><div class="oc-hint">Added on top of total production cost before profit margin.</div></div>
        </div>
      </div>

      <!-- ══ FLEXO: Label & Reel Specs ══ -->
      <div v-if="isFlexo">
        <div class="oc-divider-label">Material Dimensions</div>
        <div class="oc-2col">
          <div class="oc-field">
            <label class="oc-lbl">Reel Width (mm)</label>
            <input v-model.number="form.reel_width_mm" type="number" min="0" class="oc-inp" @change="scheduleCalc" />
          </div>
          <div class="oc-field">
            <label class="oc-lbl">Reel Length (m) <span class="oc-calc-tag">{{ form.reel_length_m > 0 ? 'manual' : 'auto' }}</span></label>
            <input type="number" v-model.number="form.reel_length_m" min="0" step="0.01" class="oc-inp"
              :placeholder="calc.sheet && calc.sheet.reel_length ? fmtQty(calc.sheet.reel_length) : 'auto'"
              @change="scheduleCalc" />
          </div>
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
          <div class="oc-field">
            <label class="oc-lbl">No of Colors <span v-if="addedColors" class="oc-total-badge">Total {{ effectiveColors }}</span></label>
            <input v-model.number="form.no_of_colors" type="number" min="0" class="oc-inp" @change="scheduleCalc" />
            <div v-if="addedColors" class="oc-hint">Base {{ form.no_of_colors || 0 }} + {{ addedColors }} from specs = <b>{{ effectiveColors }}</b> colors</div>
          </div>
          <div class="oc-field"><label class="oc-lbl">Order Quantity (stickers)</label><input v-model.number="form.item_qty" type="number" min="1" class="oc-inp" @change="scheduleCalc" /></div>
        </div>
        <div class="oc-2col">
          <div class="oc-field">
            <label class="oc-lbl">Plate Count <span class="oc-calc-tag">{{ form.plate_count_manual ? 'manual' : 'auto = colors' }}</span></label>
            <input :value="plateCount" @input="onPlateCountInput($event.target.value)" type="number" min="0" class="oc-inp" />
            <div v-if="form.plate_count_manual" class="oc-hint"><a href="#" @click.prevent="resetPlateCount">↺ reset to auto ({{ effectiveColors }})</a></div>
          </div>
          <div class="oc-field"><label class="oc-lbl">Plate Price (per plate)</label><input v-model.number="form.plate_price" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
        </div>
        <div class="oc-2col">
          <div class="oc-field"><label class="oc-lbl">Profit Margin (%)</label><input v-model.number="form.profit_margin" type="number" min="0" class="oc-inp" @change="scheduleCalc" /></div>
          <div class="oc-field"><label class="oc-lbl">Extra Production Cost (%)</label><input v-model.number="form.extra_prod_cost_pct" type="number" min="0" step="0.5" class="oc-inp" @change="scheduleCalc" /></div>
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
          <div class="oc-group-label oc-group-toggle" @click="toggleGroup(groupName)">
            <span>{{ groupName }}</span>
            <span class="oc-group-arrow">{{ collapsedGroups[groupName] ? '▶' : '▼' }}</span>
          </div>
          <div v-show="!collapsedGroups[groupName]">
          <div v-for="spec in groupSpecTreeSpecs(specs)" :key="spec.spec_name" class="oc-spec"
               :style="{ marginLeft: (specDepth(spec.spec_name) * 18) + 'px' }">
            <label class="oc-spec-label" :class="{active: isSelected(spec.spec_name)}">
              <input type="checkbox" class="oc-chk" :checked="isSelected(spec.spec_name)" @change="toggleSpec(spec.spec_name)" />
              <span class="oc-spec-name"><span v-if="specDepth(spec.spec_name)" style="color:#9ca3af">↳ </span>{{ spec.spec_name }}<span v-if="spec.adds_colors" class="oc-total-badge" style="margin-left:6px">+{{ spec.adds_colors }} color</span></span>
              <span v-if="isSelected(spec.spec_name)" class="oc-spec-chevron" @click.prevent.stop="toggleSpecCollapse(spec.spec_name)">
                {{ collapsedSpecs[spec.spec_name] ? '▶' : '▼' }}
              </span>
            </label>
            <div v-if="isSelected(spec.spec_name)" v-show="!collapsedSpecs[spec.spec_name]" class="oc-spec-detail">

              <!-- Machine assignment — opens Production Assignment dialog -->
              <div v-if="spec.has_machine" class="oc-machine-sec">
                <div class="oc-assign-row">
                  <div class="oc-assign-summary">
                    <span v-if="specState[spec.spec_name]._machine">
                      <b class="oc-assign-mname">{{ specState[spec.spec_name]._machine }}</b>
                      <span class="oc-assign-dim"> × {{ specState[spec.spec_name]._cycles || 1 }}</span>
                      <span v-if="specState[spec.spec_name]._csc" class="oc-badge-csc">CSC</span>
                      <span v-for="(foil, fi) in (specState[spec.spec_name]._foils || [])" :key="'f'+fi" class="oc-badge-ink" :style="foil.foil_group==='COLD'?'background:#bbdefb':'background:#ffe0b2'">
                        {{ foil.foil_group }} {{ foil.foil_name }}
                      </span>
                      <span v-for="(ink, ii) in (specState[spec.spec_name]._inks || [])" :key="'i'+ii" class="oc-badge-ink">
                        {{ ink.ink_name }}
                      </span>
                    </span>
                    <span v-else class="oc-no-assign">No machine assigned</span>
                  </div>
                  <button class="oc-btn-assign" @click.stop="openInkDialog(spec)">⚙ Assign</button>
                </div>
                <!-- Skip machine cost — auto-ticks per the spec's skip rules; untick to charge -->
                <label v-if="specHasSkipRule(spec)" class="oc-skip-row" :class="{active: effectiveSkip(spec.spec_name)}">
                  <input type="checkbox" class="oc-chk" :checked="effectiveSkip(spec.spec_name)" @change="toggleSkipMachine(spec.spec_name)" />
                  <span>{{ skipMachineLabel(spec.spec_name) }}</span>
                </label>
              </div>

              <div v-for="cf in spec.cost_facts" :key="cf.cost_fact" class="oc-cf">
                <!-- Manual-select cost fact: added to the calculation ONLY when ticked -->
                <label v-if="cf.manual_select" class="oc-cf-toggle" :class="{active: isCostFactIncluded(spec.spec_name, cf)}">
                  <input type="checkbox" class="oc-chk" :checked="isCostFactIncluded(spec.spec_name, cf)" @change="toggleCostFact(spec.spec_name, cf.cost_fact)" />
                  <span>{{ cf.cost_fact }}</span>
                </label>
                <template v-if="isCostFactIncluded(spec.spec_name, cf)">
                <div v-if="spec.cost_facts.length > 1 && !cf.manual_select" class="oc-cf-title">{{ cf.cost_fact }}</div>
                <!-- Item selector -->
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
                    <label class="oc-attr-lbl">{{ attr.lable || attr.attribute_name }}{{ attr.type==='Percentage' ? ' (%)' : '' }}</label>
                    <input :type="(attr.type==='Number'||attr.type==='Float'||attr.type==='Percentage')?'number':'text'" min="0" class="oc-inp"
                      :value="getAttrValue(spec.spec_name, cf.cost_fact, attr.attribute_name)"
                      @input="onAttrChange(spec.spec_name, cf.cost_fact, attr.attribute_name, $event.target.value, attr.type)" />
                  </div>
                </div>
                <div v-if="cf.master && !cf.master.items.length && !cf.master.attributes.length && cf.master.calculation" class="oc-cf-info">ℹ️ {{ cf.master.calculation }}</div>
                </template>
              </div>
            </div>
          </div>
          </div><!-- /v-show collapsible group -->
        </div>
      </div>

      <!-- ══ MANUAL COST ITEMS (ad-hoc) ══ -->
      <div class="oc-divider"></div>
      <div class="oc-divider-label">Manual Cost Items</div>
      <div v-for="(mc, mi) in manualCosts" :key="'mc'+mi" style="display:flex;gap:5px;align-items:center;margin-bottom:5px">
        <input v-model="mc.name" type="text" placeholder="Name (e.g. Delivery)" class="oc-inp" style="flex:2;min-width:0" @input="scheduleCalc" />
        <select v-model="mc.cost_group" class="oc-inp oc-sel" style="flex:1.2;min-width:0" @change="scheduleCalc">
          <option value="Production">Production</option>
          <option value="Material">Material</option>
          <option value="Preparation">Preparation</option>
        </select>
        <input v-model.number="mc.qty" type="number" min="0" placeholder="Qty" class="oc-inp" style="width:60px" @input="scheduleCalc" />
        <input v-model.number="mc.rate" type="number" min="0" placeholder="Rate" class="oc-inp" style="width:78px" @input="scheduleCalc" />
        <span style="min-width:72px;text-align:right;font-family:monospace;font-size:11px;font-weight:600">{{ fmtCur(manualCostAmount(mc)) }}</span>
        <button @click="removeManualCost(mi)" title="Remove" style="border:none;background:#fee2e2;color:#b91c1c;border-radius:4px;width:22px;height:22px;cursor:pointer;flex:none">×</button>
      </div>
      <button class="oc-btn-mini" @click="addManualCost">+ Add Manual Cost</button>
      <div v-if="!manualCosts.length" class="oc-hint" style="margin-top:3px">Add ad-hoc costs (delivery, handling, etc.). Pick a type: Production / Material / Preparation.</div>

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
        <template v-if="!viewOnly">
          <span v-if="calcLoading" class="oc-spin"></span>
          <button class="oc-btn oc-btn-green" @click="calculate" :disabled="calcLoading">⚡ {{ calcLoading ? 'Calculating…' : 'Calculate' }}</button>
          <button class="oc-btn oc-btn-blue"  @click="saveCosting" :disabled="saveLoading">💾 {{ saveLoading ? 'Saving…' : 'Save' }}</button>
        </template>
        <button v-else class="oc-btn oc-btn-print" @click="printPage">🖨 Print / PDF</button>
      </div>
    </div>
    <div class="oc-pb">

      <div v-if="savedDocName && !viewOnly" class="oc-saved-banner">
        ✓ Saved as <strong>{{ savedDocName }}</strong>
        <button class="oc-link-btn" @click="openDoc">Open Document →</button>
      </div>
      <div v-if="viewOnly && savedDocName" class="oc-view-banner">
        👁 VIEW ONLY — {{ savedDocName }}
      </div>

      <!-- Offset: Sheet requirements -->
      <div v-if="calc.sheet && calc.sheet.full_sheet_qty" class="oc-result-card">
        <div class="oc-rc-title oc-rc-toggle" @click="sheetExpanded = !sheetExpanded">
          Sheet Requirements
          <span class="oc-collapse-chevron">{{ sheetExpanded ? '▴' : '▾' }}</span>
        </div>
        <div v-show="sheetExpanded" class="oc-kv-grid" style="margin-top:8px">
          <div class="oc-kv"><span class="k">Cut Sheet Ups</span><span class="v">{{ calc.sheet.cut_sheet_ups }}</span></div>
          <div class="oc-kv"><span class="k">Cut Sheet Qty</span><span class="v">{{ fmtNum(calc.sheet.cut_sheet_qty) }}</span></div>
          <div class="oc-kv"><span class="k">Wastage</span><span class="v">{{ fmtNum(calc.sheet.wastage) }}</span></div>
          <div class="oc-kv"><span class="k">Req. Cut Sheets</span><span class="v">{{ fmtNum(calc.sheet.req_cut_sheets) }}</span></div>
          <div class="oc-kv"><span class="k">Full Sheet Qty</span><span class="v oc-v-blue">{{ fmtNum(calc.sheet.full_sheet_qty) }}</span></div>
        </div>
      </div>

      <!-- Flexo: Reel requirements -->
      <div v-if="calc.sheet && calc.sheet.reel_area" class="oc-result-card">
        <div class="oc-rc-title oc-rc-toggle" @click="flexoExpanded = !flexoExpanded">
          Reel Requirements
          <span class="oc-collapse-chevron">{{ flexoExpanded ? '▴' : '▾' }}</span>
        </div>
        <div v-show="flexoExpanded" class="oc-kv-grid" style="margin-top:8px">
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
                <th>UOM</th>
                <th class="r">Rate</th>
                <th class="r">Amount (LKR)</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="grp in groupedCostRows" :key="grp.group">
                <tr class="oc-grp-header">
                  <td colspan="7">{{ grp.group }} Costs</td>
                  <td class="r mono">{{ fmtCur(grp.subtotal) }}</td>
                </tr>
                <tr v-for="row in grp.rows" :key="grp.group + '-' + row.spec_name + '-' + row.cost_fact" :class="row.is_auto ? 'oc-auto-row' : ''">
                  <td>{{ row.spec_name }}<span v-if="row.is_auto" class="oc-auto-tag">auto</span></td>
                  <td>{{ row.cost_fact }}</td>
                  <td class="oc-grp">{{ row.cost_group }}</td>
                  <td>
                    <span v-if="row.selected_item_name && row.selected_item_name !== row.selected_item">
                      {{ row.selected_item_name }}
                      <span class="oc-code-badge">{{ row.selected_item }}</span>
                    </span>
                    <span v-else>{{ row.selected_item }}</span>
                  </td>
                  <td class="r mono">{{ fmtQty(row.req_qty) }}</td>
                  <td class="oc-uom">{{ row.uom || '' }}</td>
                  <td class="r mono">{{ fmtRate(row.rate) }}</td>
                  <td class="r mono">{{ fmtCur(row.amount) }}</td>
                </tr>
              </template>
              <tr class="oc-tot"><td colspan="7"><strong>TOTAL COST</strong></td><td class="r mono">{{ fmtCur(calc.group_totals.grand) }}</td></tr>
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
/* Page-head removal — scoped to calculator wrapper only */
.oc-page-wrapper .page-head{display:none!important}
.oc-page-wrapper .layout-main-section-wrapper,.oc-page-wrapper .layout-main-section{padding:0!important;margin:0!important}
/* Layout: both panels fill viewport height and scroll independently */
.oc-wrap{display:grid;grid-template-columns:430px 1fr;gap:10px;padding:8px 10px;height:calc(100vh - 52px);overflow:hidden;font-size:13px;box-sizing:border-box}
@media(max-width:960px){.oc-wrap{grid-template-columns:1fr;height:auto;overflow:visible}}
.oc-panel{background:#fff;border:1px solid var(--border-color,#d1d5db);border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.06);display:flex;flex-direction:column;overflow:hidden;min-height:0}
.oc-ph{display:flex;align-items:center;gap:8px;padding:9px 14px;background:var(--subtle-fg,#f8fafc);border-bottom:1px solid var(--border-color,#d1d5db);flex-shrink:0}
.oc-ph-split{justify-content:space-between}
.oc-pt{font-weight:700;font-size:13px;color:var(--text-color,#1f272e)}
.oc-badge{font-size:10px;font-weight:700;background:#2c7be5;color:#fff;border-radius:4px;padding:2px 7px;letter-spacing:.05em}
.oc-pb{padding:12px 14px;overflow-y:auto;flex:1;min-height:0}
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
.oc-group-toggle{cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:4px 6px;border-radius:4px;user-select:none}
.oc-group-toggle:hover{background:#f0f4ff;color:#1a3a5c}
.oc-group-arrow{font-size:9px;color:#9ca3af;transition:transform .15s}
.oc-chips-label{font-size:10px;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px}
.oc-calc-val{background:#f0f4ff;border:1px solid #dde4f0;border-radius:4px;padding:6px 10px;font-family:monospace;font-size:13px;font-weight:600;color:#1a3a5c;min-height:34px;display:flex;align-items:center}
.oc-calc-tag{font-size:9px;font-weight:400;background:#e0eaff;color:#3b5bdb;border-radius:3px;padding:1px 5px;margin-left:4px;text-transform:uppercase;letter-spacing:.04em;vertical-align:middle}
.oc-spec{border-radius:5px;margin-bottom:3px;overflow:hidden;border:1px solid transparent}
.oc-spec:has(.oc-spec-label.active){border-color:#2c7be5}
.oc-spec-label{display:flex;align-items:center;gap:8px;padding:7px 10px;cursor:pointer;border-radius:5px;background:#f8fafc;transition:background .12s;user-select:none}
.oc-spec-label.active{background:#e8f0fd}
.oc-spec-label:hover{background:#f0f4ff}
.oc-spec-name{flex:1;font-weight:600;font-size:13px;color:#1f272e}
.oc-spec-chevron{font-size:9px;color:#9ca3af;padding:2px 4px;border-radius:3px;cursor:pointer;line-height:1}
.oc-spec-chevron:hover{background:#e0eaff;color:#1a3a5c}
/* Machine assign row */
.oc-assign-row{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:4px 0}
.oc-assign-summary{flex:1;font-size:12px;min-width:0}
.oc-assign-mname{color:#1a3a5c}
.oc-assign-dim{color:#888;font-size:11px}
.oc-no-assign{color:#aaa;font-style:italic;font-size:12px}
.oc-badge-csc{background:#fff3cd;color:#856404;border-radius:3px;padding:1px 5px;font-size:10px;margin-left:4px}
.oc-badge-ink{background:#e3f2fd;color:#1565c0;border-radius:3px;padding:1px 5px;font-size:10px;margin-left:4px}
.oc-btn-assign{padding:4px 10px;background:#1a3a5c;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;white-space:nowrap}
.oc-btn-assign:hover{background:#2c5f8a}
/* Production Assignment dialog */
.oc-pa-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9999;display:flex;align-items:center;justify-content:center}
.oc-pa-dialog{background:#fff;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.18);width:460px;max-width:95vw;max-height:90vh;display:flex;flex-direction:column}
.oc-pa-hdr{padding:14px 18px;background:#1a3a5c;color:#fff;border-radius:8px 8px 0 0;font-size:14px;font-weight:600}
.oc-pa-body{padding:16px 18px;overflow-y:auto;flex:1}
.oc-pa-ftr{padding:10px 18px;border-top:1px solid #e5e7eb;display:flex;justify-content:flex-end;gap:8px}
.oc-hint{font-size:11px;color:#888;margin-top:3px;font-style:italic}
.oc-pa-section-hdr{font-size:11px;font-weight:700;color:#1a3a5c;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;padding-bottom:3px;border-bottom:1px solid #e5e7eb}
.oc-no-inks{color:#aaa;font-style:italic;font-size:12px;padding:4px 0}
.oc-ink-chip{display:flex;align-items:center;justify-content:space-between;background:#e3f2fd;border-radius:5px;padding:5px 10px;margin-bottom:4px}
.oc-ink-chip-label{font-size:13px;color:#1565c0;font-weight:500}
.oc-ink-pct{font-size:11px;color:#1976d2;font-weight:400}
.oc-ink-rm{background:transparent;border:none;cursor:pointer;font-size:14px;padding:0 2px;opacity:.7}
.oc-ink-rm:hover{opacity:1}
.oc-add-ink{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.oc-ink-sel{flex:1;min-width:140px}
.oc-pct-inp{width:60px!important}
.oc-pct-sym{font-size:13px;color:#555;margin-left:-4px}
.oc-btn-cancel{padding:6px 14px;background:#f3f4f6;color:#374151;border:1px solid #d1d5db;border-radius:4px;cursor:pointer;font-size:13px}
.oc-btn-cancel:hover{background:#e5e7eb}
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
/* Machine section inside spec detail */
.oc-machine-sec{background:#fff8e1;border:1px solid #ffe082;border-radius:5px;padding:8px 10px;margin-bottom:10px}
.oc-skip-row{display:flex;align-items:center;gap:7px;margin-top:7px;padding-top:7px;border-top:1px dashed #e6d08a;font-size:11.5px;color:#6b5900;cursor:pointer}
.oc-skip-row.active{color:#8a5a00;font-weight:600}
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
.oc-tbl-scroll{overflow:auto;max-height:38vh;border-radius:4px;border:1px solid #d1d5db}
.oc-tbl{width:100%;border-collapse:collapse;font-size:12px}
.oc-tbl thead tr{background:#1a3a5c}
.oc-grp-header td{background:#1a3a5c!important;color:#fff;font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding:6px 8px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.oc-cf-toggle{display:flex;align-items:center;gap:7px;padding:5px 9px;border:1px solid #e5e7eb;border-radius:5px;cursor:pointer;font-size:12px;font-weight:600;color:#374151;margin-bottom:6px}
.oc-cf-toggle.active{background:#eef4ff;border-color:#93c5fd;color:#1a3a5c}
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
/* Collapsible result cards */
.oc-rc-toggle{cursor:pointer;display:flex;justify-content:space-between;align-items:center;user-select:none}
.oc-rc-toggle:hover{color:#2c7be5}
.oc-collapse-chevron{font-size:11px;opacity:.6;margin-left:6px;transition:transform .15s}
.oc-hint{font-size:11px;color:#6b7280;margin-top:3px;display:block}
/* View-only mode */
.oc-view-overlay{pointer-events:none;opacity:.82;user-select:none}
.oc-view-banner{background:#1a3a5c;color:#fff;padding:8px 14px;font-size:12px;font-weight:600;border-radius:4px;display:flex;align-items:center;gap:6px;margin-bottom:12px;letter-spacing:.02em}
.oc-btn-print{background:#1a3a5c;color:#fff}.oc-btn-print:hover{filter:brightness(1.2)}
.oc-breakdown-wrap{margin-top:4px;margin-bottom:8px}
.oc-chips-row{display:flex;flex-wrap:wrap;gap:5px;align-items:center;min-height:28px}
.oc-chip-base{background:#1a73e8;color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600}
.oc-chip-extra{background:#455a64;color:#fff;padding:3px 6px 3px 10px;border-radius:12px;font-size:12px;display:flex;align-items:center;gap:4px}
.oc-chip-x{background:transparent;border:none;color:#fff;cursor:pointer;font-size:14px;padding:0 2px;line-height:1;opacity:.8}
.oc-chip-x:hover{opacity:1}
.oc-chip-adder{display:flex;align-items:center;gap:4px}
.oc-chip-inp{width:80px;padding:3px 6px;border:1px solid #ccc;border-radius:4px;font-size:12px;height:26px}
.oc-btn-mini{padding:3px 8px;background:#43a047;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;height:26px}
.oc-btn-mini:hover{background:#388e3c}
.oc-total-badge{font-size:11px;background:#e8f5e9;color:#2e7d32;padding:1px 6px;border-radius:8px;font-weight:600;margin-left:6px}
.oc-uom{color:#888;font-size:11px;text-align:center;white-space:nowrap}
/* Print */
@media print{
  body>.navbar,body>.container>.page-container>.page-head,
  .oc-view-banner,.oc-ph .oc-btn-back,
  .oc-wrap .oc-panel:first-child{display:none!important}
  .oc-wrap{height:auto!important;overflow:visible!important;display:block!important;padding:0!important;grid-template-columns:none!important}
  .oc-panel{height:auto!important;overflow:visible!important;border:none!important;box-shadow:none!important}
  .oc-pb{overflow:visible!important;height:auto!important}
  .oc-tbl-scroll{max-height:none!important;overflow:visible!important}
  .oc-ph{border-bottom:1px solid #ccc!important;margin-bottom:8px}
  .oc-result-card{page-break-inside:avoid;border:1px solid #ccc!important}
  .oc-sell-badge{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .oc-tbl thead tr,.oc-ptbl thead tr,.oc-tot td,.oc-sub td{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
	`;
	document.head.appendChild(s);
}
