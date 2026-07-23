// Copyright (c) 2026, Techincglobal.com
// BOM Builder — SFG Chain Edition
// Route: /app/bom-builder?so=SO-xxx  OR  ?quotation=SQ-xxx

frappe.pages['bom-builder'].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'BOM Builder',
		single_column: true,
	});
	$(wrapper).addClass('bb-page-wrapper');
	$(wrapper).find('.page-head').hide();
	var mountEl = document.createElement('div');
	mountEl.id = 'bb-mount';
	page.main[0].appendChild(mountEl);
	bb_inject_styles();
	bb_mount_app(mountEl);
};

frappe.pages['bom-builder'].on_page_show = function (wrapper) {
	if (window.__bb_app__) window.__bb_app__.loadFromUrl();
};

// ─────────────────────────────────────────────────────────────
function bb_mount_app(el) {

	var API = {
		getContext:      'nxtgen_savinda_pricing_calculator.api.bom_builder.get_bom_context',
		getBomData:      'nxtgen_savinda_pricing_calculator.api.bom_builder.get_bom_data',
		createBomChain:  'nxtgen_savinda_pricing_calculator.api.bom_builder.create_bom_chain',
		createMultiBom:  'nxtgen_savinda_pricing_calculator.api.bom_builder.create_multi_bom',
		searchCB:        'nxtgen_savinda_pricing_calculator.api.bom_builder.search_cb_for_fg',
		saveBomConfig:   'nxtgen_savinda_pricing_calculator.api.bom_builder.save_bom_config',
		loadBomConfig:   'nxtgen_savinda_pricing_calculator.api.bom_builder.load_bom_config',
		checkExistingBom:'nxtgen_savinda_pricing_calculator.api.bom_builder.check_existing_bom',
		disableBom:      'nxtgen_savinda_pricing_calculator.api.bom_builder.disable_bom',
	};

	function fmt(v) { return parseFloat(v || 0).toLocaleString('en-LK', { minimumFractionDigits: 2, maximumFractionDigits: 4 }); }
	function fmtq(v) { var n = parseFloat(v || 0); if (n === 0) return '0'; if (Math.abs(n) >= 1) return n.toLocaleString('en-LK', {minimumFractionDigits: 0, maximumFractionDigits: 4}); return n.toPrecision(4); }
	function fmtN(v) { return Math.round(parseFloat(v) || 0).toLocaleString('en-LK'); }
	function sanitize(s) { return (s || '').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '').toUpperCase().substring(0, 20); }

	var app = Vue.createApp({
		data() {
			return {
				loading: false, saving: false, error: '',
				sourceType: '', sourceName: '', customer: '',
				fgItems: [], selectedFG: '', selectedFGs: [], calcBreakdown: '', mfgQty: 0,
				itemQty: 0,
				globalCB: '',
				isDefault: false,
				qtyChanged: false,   // true when mfgQty changed but not recalculated
				operations: [],
				extraMaterials: [],
				baseMat: '', baseMatName: '', baseMatQty: 0, baseMatUom: 'Nos',
				sheetData: {},    // sheet calculation results for display
				sheetExpanded: true,
				dragIdx: null,
			};
		},
		computed: {
			selectedFGData() { return this.fgItems.find(function (f) { return f.item_code === this.selectedFG; }, this) || null; },
			canCreate() { return this.selectedFG && this.mfgQty > 0 && this.operations.length > 0; },
			costItem() {
				var fg = this.selectedFGData;
				return fg ? (fg.cost_item || '') : '';
			},
		},

		watch: {
			mfgQty: function (newVal, oldVal) {
				// Flag that qty changed so user knows to recalculate
				if (oldVal && newVal !== oldVal && this.operations.length) {
					this.qtyChanged = true;
				}
			},
		},
		mounted() { this.loadFromUrl(); },
		methods: {
			fmt, fmtq, fmtN,

			loadFromUrl() {
				var p = new URLSearchParams(window.location.search);
				var so = p.get('so'), q = p.get('quotation'), cs = p.get('cost_sheet') || p.get('cs');
				if (so)      { this.sourceType = 'Sales Order';       this.sourceName = so; }
				else if (q)  { this.sourceType = 'Savinda Quotation'; this.sourceName = q; }
				else if (cs) { this.sourceType = 'Cost Sheet';        this.sourceName = cs; }
				else         { this.error = 'No source document. Open from a Sales Order, NPD-related Cost Sheet or Savinda Quotation.'; return; }
				this.loadContext();
			},

			loadContext() {
				var self = this; self.loading = true; self.error = '';
				frappe.call({
					method: API.getContext,
					args: { source_type: self.sourceType, source_name: self.sourceName },
					callback: function (r) {
						self.loading = false;
						if (!r.message) return;
						self.customer = r.message.customer;
						self.fgItems  = r.message.fg_items || [];
						if (self.fgItems.length === 1) {
							self.selectedFG = self.fgItems[0].item_code;
							self.selectedFGs = [self.selectedFG];
							self.$nextTick(function () { self.onFGChange(); });
						}
					},
					error: function () { self.loading = false; self.error = 'Failed to load context.'; },
				});
			},

			// Dropdown selection = single-FG build
			onFGDropdown() {
				this.selectedFGs = this.selectedFG ? [this.selectedFG] : [];
				this.onFGChange();
			},

			onFGChange() {
				var self = this;
				var fg = self.selectedFGData;
				if (!fg) return;
				self.mfgQty        = fg.qty || 0;
				self.calcBreakdown = fg.calculation_breakdown || self.globalCB || '';
				self.operations    = [];
				self.extraMaterials= [];
				self.baseMat = ''; self.baseMatName = '';

				// ALWAYS load fresh quantities from CB (never use saved config for qtys)
				// saved config customisations (SFG names, split points, materials) are overlaid after
				if (self.calcBreakdown && self.mfgQty) {
					self.loadBomData();
				} else {
					frappe.show_alert({ message: 'No CB linked for ' + self.selectedFG + '. Set "Global CB" or click 🔍.', indicator: 'orange' }, 6);
				}
			},

			// Multi-select FG popup — one checkbox per FG (all pre-ticked = select all)
			openFGPicker() {
				var self = this;
				if (!self.fgItems.length) { frappe.msgprint('No FG items to select.'); return; }
				var had = self.selectedFGs.length > 0;
				var fields = [{
					fieldtype: 'HTML',
					options: '<div style="font-size:12px;color:#555;margin-bottom:4px">Tick the FGs to build. Selecting several variants of the same product builds a <b>consolidated multi-level BOM</b> that shares the common SFGs.</div>',
				}];
				self.fgItems.forEach(function (f, i) {
					fields.push({
						fieldtype: 'Check', fieldname: 'fg_' + i,
						// default: keep prior selection if any, else select all
						default: had ? (self.selectedFGs.indexOf(f.item_code) > -1 ? 1 : 0) : 1,
						label: f.item_code + ' — ' + (f.item_name || '')
							+ (f.cost_item ? '   [Cost Item: ' + f.cost_item + ']' : ''),
					});
				});
				var d = new frappe.ui.Dialog({
					title: 'Select Finished Goods to build',
					size: 'large',
					fields: fields,
					primary_action_label: 'Select',
					primary_action: function (v) {
						var picked = self.fgItems
							.filter(function (f, i) { return v['fg_' + i]; })
							.map(function (f) { return f.item_code; });
						if (!picked.length) { frappe.msgprint('Tick at least one FG.'); return; }
						d.hide();
						self.selectedFGs = picked;
						self.selectedFG = picked[0];   // chain loads from the first
						self.$nextTick(function () { self.onFGChange(); });
					},
				});
				d.show();
			},

			saveConfig() {
				var self = this;
				if (!self.selectedFG || !self.operations.length) {
					frappe.msgprint('No operations to save.'); return;
				}
				frappe.call({
					method: API.saveBomConfig,
					args: {
						fg_item:       self.selectedFG,
						operations:    JSON.stringify(self.operations),
						pricing_type:  self.sourceType === 'Savinda Quotation' ? 'Offset' : 'Offset',
					},
					callback: function () {
						frappe.show_alert({ message: 'BOM config saved for ' + self.selectedFG, indicator: 'green' });
					},
				});
			},

			applyGlobalCB() {
				// Stamp globalCB on all fg items that don't have their own CB
				var self = this;
				if (!self.globalCB) return;
				self.fgItems.forEach(function (fg) {
					if (!fg.calculation_breakdown) fg.calculation_breakdown = self.globalCB;
				});
				// If current FG has no CB, load now
				if (!self.calcBreakdown && self.selectedFG && self.mfgQty) {
					self.calcBreakdown = self.globalCB;
					self.loadBomData();
				}
				frappe.show_alert({ message: 'Global CB applied to all FGs.', indicator: 'green' });
			},

			pickCB() {
				var self = this;
				var target = self.selectedFG || 'all FGs';
				frappe.call({
					method: API.searchCB,
					args: { fg_item_code: self.selectedFG || '', search_term: '' },
					callback: function (r) {
						var rows = r.message || [];
						var d = new frappe.ui.Dialog({
							title: 'Select Calculation Breakdown',
							fields: [
								{
									fieldtype: 'Link', fieldname: 'cb',
									label: 'Calculation Breakdown', options: 'Calculation Breakdown', reqd: 1,
									description: rows.length ? rows.map(function(r){ return r.calculation_breakdown; }).join(', ') : '',
								},
								{
									fieldtype: 'Check', fieldname: 'apply_all',
									label: 'Apply to all FGs in this list (use when all FGs share one CB)',
									default: 1,
								},
							],
							primary_action_label: 'Use CB',
							primary_action: function (v) {
								d.hide();
								var cb = (v.cb || '').trim();
								if (!cb) return;
								if (v.apply_all) {
									self.globalCB = cb;
									self.fgItems.forEach(function (fg) { fg.calculation_breakdown = cb; });
								}
								self.calcBreakdown = cb;
								if (self.mfgQty) self.loadBomData();
							},
						});
						d.show();
						// Pre-fill if rows found
						if (rows.length) {
							self.$nextTick(function () {
								try { d.set_value('cb', rows[0].calculation_breakdown); } catch(e) {}
							});
						}
					},
				});
			},

			loadBomData(preserveOrder) {
				var self = this;
				if (!self.calcBreakdown || !self.mfgQty) return;
				self.loading = true;
				self.qtyChanged = false;
				frappe.call({
					method: API.getBomData,
					args: { calculation_breakdown: self.calcBreakdown, mfg_qty: self.mfgQty, fg_item: self.selectedFG },
					callback: function (r) {
						self.loading = false;
						if (!r.message) return;
						var d = r.message;
								var ops = d.operations || [];
						ops.forEach(function (op) {
							if (!Array.isArray(op.materials))          op.materials = [];
							if (!('split_to_item_unit' in op))         op.split_to_item_unit = false;
						});
						// Always refresh header/sheet + base material from the fresh calc
						self.itemQty     = d.item_qty || self.mfgQty;
						self.sheetData   = d.sheet || {};
						self.baseMat     = d.base_material      || '';
						self.baseMatName = d.base_material_name || '';
						self.baseMatQty  = d.base_material_qty  || 0;
						self.baseMatUom  = d.base_material_uom  || 'Nos';

						// ── Recalculate WITHOUT changing the arranged order ──
						// Merge fresh quantities/rates into the existing ops (matched by
						// spec_name); keep the current order, markers, SFG names, materials.
						if (preserveOrder && self.operations.length) {
							var freshMap = {};
							ops.forEach(function (fo) { if (!(fo.spec_name in freshMap)) freshMap[fo.spec_name] = fo; });
							self.operations.forEach(function (op) {
								var fo = freshMap[op.spec_name];
								if (!fo) return;
								op.input_qty    = fo.input_qty;
								op.input_uom    = fo.input_uom;
								op.output_qty   = fo.output_qty;
								op.output_uom   = fo.output_uom;
								op.time_in_mins = fo.time_in_mins;
								op.hour_rate    = fo.hour_rate;
								if (fo.machine)     op.machine     = fo.machine;
								if (fo.workstation) op.workstation = fo.workstation;
							});
							self.syncChain();
							frappe.show_alert({ message: '✓ Quantities recalculated — order kept.', indicator: 'green' }, 3);
							return;
						}

						// Sort: Print first (only on a fresh load)
						ops.sort(function (a, b) { return (a.is_print ? 0 : 1) - (b.is_print ? 0 : 1); });
						self.operations  = ops;
						self.extraMaterials = d.raw_materials || [];
						self.syncChain();

						// Overlay saved customisations (SFG names, split points, material selections)
						// We do NOT restore qtys from saved config — those always come from the CB
						frappe.call({
							method: API.loadBomConfig,
							args:   { fg_item: self.selectedFG },
							callback: function (rc) {
								if (!rc.message || !rc.message.operations) return;
								var savedOps = rc.message.operations;
								// Match by spec_name and overlay user customisations
								self.operations.forEach(function (op) {
									var saved = savedOps.find(function(s){ return s.spec_name === op.spec_name; });
									if (!saved) return;
									if (saved.sfg_code)          op.sfg_code = saved.sfg_code;
									if (saved.sfg_name)          op.sfg_name = saved.sfg_name;
									if (saved.split_to_item_unit) op.split_to_item_unit = true;
									if (saved.common_sfg_point)  op.common_sfg_point = true;
									if (saved.output_is_fg)      op.output_is_fg = true;
									if (Array.isArray(saved.materials) && saved.materials.length)
										op.materials = saved.materials;
									// Use 'in' so explicit false/empty from saved config wins over spec defaults
									if ('exclude_from_bom' in saved)            op.exclude_from_bom            = saved.exclude_from_bom;
									if ('erp_operation' in saved)               op.erp_operation               = saved.erp_operation;
									if ('has_quality_inspection' in saved)      op.has_quality_inspection      = saved.has_quality_inspection;
									if ('quality_inspection_template' in saved) op.quality_inspection_template = saved.quality_inspection_template;
								});
								self.syncChain();
								frappe.show_alert({ message: '✓ Customisations restored from saved config', indicator: 'blue' }, 3);
							},
						});
					},
					error: function () { self.loading = false; },
				});
			},

			// ── SFG chain sync — called after any order/name change ──
			syncChain() {
				var self = this;
				var afterSplit = false;

				self.operations.forEach(function (op, i) {
					if (!Array.isArray(op.materials))           op.materials = [];
					if (!('split_to_item_unit' in op))          op.split_to_item_unit = false;
					if (!('exclude_from_bom' in op))            op.exclude_from_bom = false;
					if (!('common_sfg_point' in op))            op.common_sfg_point = false;
					if (!('output_is_fg' in op))                op.output_is_fg = false;
					if (!('erp_operation' in op))               op.erp_operation = '';
					if (!('has_quality_inspection' in op))      op.has_quality_inspection = false;
					if (!('quality_inspection_template' in op)) op.quality_inspection_template = '';

					// Auto-generate SFG names
					if (!op.sfg_code && self.selectedFG)
						op.sfg_code = self.selectedFG + '-' + sanitize(op.spec_name) + '-SFG';
					if (!op.sfg_name && self.selectedFG)
						op.sfg_name = (self.selectedFGData ? self.selectedFGData.item_name : self.selectedFG) + ' — ' + op.spec_name;

					// Update input chain
					if (i === 0) {
						// The FIRST operation always consumes the base material (full sheet),
						// at the full-sheet quantity from the costing calculation — whatever
						// operation ends up first after reordering.
						if (self.baseMat) {
							op.input_item_code = self.baseMat;
							op.input_item_name = self.baseMatName;
						}
						if (self.baseMatQty > 0) {
							op.input_qty = self.baseMatQty;
							op.input_uom = self.baseMatUom;
						}
					} else {
						var prev = self.operations[i - 1];
						op.input_item_code = prev.sfg_code;
						op.input_item_name = prev.sfg_name;
						if (!afterSplit) {
							op.input_qty = prev.output_qty;
							op.input_uom = prev.output_uom;
						} else {
							// After split: qty is item_qty
							op.input_qty = self.itemQty || self.mfgQty;
							op.input_uom = 'Nos';
						}
					}

					// Handle split point: output changes to item_qty
					if (op.split_to_item_unit && !afterSplit) {
						op.output_qty = self.itemQty || self.mfgQty;
						op.output_uom = 'Nos';
						afterSplit = true;
					} else if (afterSplit) {
						op.output_qty = self.itemQty || self.mfgQty;
						op.output_uom = 'Nos';
					}
				});
			},

			setSplitPoint(idx) {
				// Toggle split_to_item_unit — only ONE can be active
				var wasActive = this.operations[idx].split_to_item_unit;
				this.operations.forEach(function (op) { op.split_to_item_unit = false; });
				if (!wasActive) this.operations[idx].split_to_item_unit = true;
				this.syncChain();
			},

			// Final Common SFG point — ops up to & incl. this one share a common SFG
			// across all FGs; ops after it get per-FG SFGs. Only ONE can be active.
			setCommonSfg(idx) {
				var wasActive = this.operations[idx].common_sfg_point;
				this.operations.forEach(function (op) { op.common_sfg_point = false; });
				if (!wasActive) this.operations[idx].common_sfg_point = true;
				this.syncChain();
			},

			// Output is FG — this operation produces the Finished Good (no separate
			// final FG BOM). Usually the last/sorting step. Only ONE can be active.
			setOutputFg(idx) {
				var wasActive = this.operations[idx].output_is_fg;
				this.operations.forEach(function (op) { op.output_is_fg = false; });
				if (!wasActive) this.operations[idx].output_is_fg = true;
				this.syncChain();
			},

			// ── Drag-and-drop ──
			onDragStart(idx) { this.dragIdx = idx; },
			onDragOver(e)    { e.preventDefault(); },
			onDrop(targetIdx) {
				if (this.dragIdx === null || this.dragIdx === targetIdx) return;
				var ops = this.operations.slice();
				var moved = ops.splice(this.dragIdx, 1)[0];
				ops.splice(targetIdx, 0, moved);
				this.operations = ops;
				this.dragIdx = null;
				this.syncChain();
			},
			onSFGChange(op) {
				// When user changes SFG name/code, re-sync downstream
				this.syncChain();
			},
			removeOp(idx) { this.operations.splice(idx, 1); this.syncChain(); },

			// Add an operation / SFG that isn't in the costing breakdown
			addOperation() {
				var self = this;
				frappe.prompt([
					{ fieldtype: 'Data', fieldname: 'name', label: 'Operation / SFG Name', reqd: 1 },
					{ fieldtype: 'Link', fieldname: 'workstation', label: 'Workstation', options: 'Workstation' },
					{ fieldtype: 'Link', fieldname: 'erp_operation', label: 'ERPNext Operation', options: 'Operation',
					  description: 'Leave blank to auto-create from the name.' },
				], function (v) {
					var nm = v.name;
					self.operations.push({
						spec_name: nm, machine: '', workstation: v.workstation || '',
						time_in_mins: 60,
						input_item_code: '', input_item_name: '', input_qty: 0, input_uom: 'Nos',
						sfg_code: self.selectedFG ? (self.selectedFG + '-' + sanitize(nm) + '-SFG') : (sanitize(nm) + '-SFG'),
						sfg_name: (self.selectedFGData ? self.selectedFGData.item_name + ' — ' : '') + nm,
						output_qty: self.itemQty || self.mfgQty, output_uom: 'Nos',
						materials: [], is_print: false, no_of_colors: 0,
						split_to_item_unit: false, exclude_from_bom: false, output_is_fg: false,
						erp_operation: v.erp_operation || '', has_quality_inspection: false, quality_inspection_template: '',
						manual: true,
					});
					self.syncChain();
					frappe.show_alert({ message: 'Operation added — drag it to the right position in the chain.', indicator: 'blue' }, 4);
				}, 'Add Operation / SFG', 'Add');
			},
			removeExtraMat(idx) { this.extraMaterials.splice(idx, 1); },
			addExtraMat() { this.extraMaterials.push({ item_code: '', item_name: '', qty: 0, rate: 0, uom: 'Nos' }); },
			addOpMat(op) { op.materials.push({ item_code: '', item_name: '', qty: 0, uom: 'Nos', include: true }); },
			removeOpMat(op, idx) { op.materials.splice(idx, 1); },

			searchOpMatItem(mat) {
				frappe.prompt([{
					fieldtype: 'Link', fieldname: 'item', label: 'Item', options: 'Item', reqd: 1,
					default: mat.item_code,
				}], function (v) {
					mat.item_code = v.item;
					frappe.call({
						method: 'frappe.client.get_value',
						args: { doctype: 'Item', fieldname: ['item_name', 'stock_uom'], filters: { name: v.item } },
						callback: function (r) {
							if (r.message) {
								mat.item_name = r.message.item_name;
								mat.uom = mat.uom || r.message.stock_uom || 'Nos';
							}
						},
					});
				}, 'Select Item', 'Set');
			},

			pickOperation(op) {
				frappe.prompt([{
					fieldtype: 'Link', fieldname: 'operation', label: 'ERPNext Operation',
					options: 'Operation', reqd: 0,
					default: op.erp_operation || op.spec_name,
					description: 'Select from Manufacturing › Operation. Leave blank to auto-create from spec name.',
				}], function (v) {
					if (!v.operation) return;
					op.erp_operation = v.operation;
					// Auto-fill workstation from the selected operation if not already set
					frappe.call({
						method: 'frappe.client.get_value',
						args: { doctype: 'Operation', fieldname: 'workstation', filters: { name: v.operation } },
						callback: function (r) {
							if (r.message && r.message.workstation && !op.workstation)
								op.workstation = r.message.workstation;
						},
					});
				}, 'Select ERPNext Operation', 'Set');
			},

			pickQITemplate(op) {
				frappe.prompt([{
					fieldtype: 'Link', fieldname: 'template', label: 'Quality Inspection Template',
					options: 'Quality Inspection Template', reqd: 0,
					default: op.quality_inspection_template || '',
					description: 'Will be set on the SFG Item master when BOM chain is created.',
				}], function (v) {
					if (v.template) op.quality_inspection_template = v.template;
				}, 'Quality Inspection Template', 'Set');
			},

			// ── Create BOM chain ──
			createBOM() {
				var self = this;
				if (!self.canCreate) return;

				// Multiple FGs → consolidated multi-FG build (backend guards per-FG)
				if (self.selectedFGs.length > 1) { self._doCreateMulti(); return; }

				// Check for existing active BOM first
				frappe.call({
					method: API.checkExistingBom,
					args:   { fg_item: self.selectedFG },
					callback: function (r) {
						if (r.message && r.message.name) {
							var bom = r.message;
							var d = new frappe.ui.Dialog({
								title: 'Existing BOM Found',
								fields: [{
									fieldtype: 'HTML',
									options: '<div style="padding:10px;background:#fff3cd;border-radius:5px;margin-bottom:10px">'
										+ '<b>⚠ Active BOM exists:</b> <a href="/app/bom/' + bom.name + '" target="_blank">' + bom.name + '</a><br>'
										+ 'Qty: ' + bom.quantity + ' | Item: ' + bom.item
										+ '</div>'
										+ '<p>What would you like to do?</p>',
								}],
								primary_action_label: '🔴 Disable Old → Create New',
								primary_action: function () {
									d.hide();
									frappe.call({
										method: API.disableBom,
										args:   { bom_name: bom.name },
										callback: function () {
											frappe.show_alert({ message: 'BOM ' + bom.name + ' disabled.', indicator: 'orange' });
											self._doCreateBOM();
										},
									});
								},
								secondary_action_label: 'Cancel',
								secondary_action: function () { d.hide(); },
							});
							d.show();
						} else {
							self._doCreateBOM();
						}
					},
				});
			},

			// Consolidated build for several FGs (common SFGs shared)
			_doCreateMulti() {
				var self = this;
				var msg = 'Create a <b>consolidated BOM</b> for <b>' + self.selectedFGs.length + ' FGs</b>?<br>'
					+ 'Common SFGs (up to the 🧩 point) are shared; each FG gets its own tail + FG BOM.<br>'
					+ 'FGs: ' + self.selectedFGs.join(', ');
				frappe.confirm(msg, function () {
					self.saving = true;
					frappe.call({
						method: API.createMultiBom,
						args: {
							fg_items:        JSON.stringify(self.selectedFGs),
							cost_item:       self.costItem || '',
							mfg_qty:         self.mfgQty,
							operations:      JSON.stringify(self.operations),
							extra_materials: JSON.stringify(self.extraMaterials),
							is_default:      self.isDefault ? 1 : 0,
						},
						freeze: true,
						freeze_message: 'Creating consolidated BOM…',
						callback: function (r) {
							self.saving = false;
							if (!r.message) return;
							var result = r.message;
							var lines = (result.created_boms || []).map(function (b) {
								if (b.skipped) return '⏭ Skipped ' + b.item + ' (' + b.skipped + ')';
								var tag = b.common ? '🧩 Common: ' : (b.is_fg ? '🏁 FG: ' : '✓ SFG: ');
								return (b.reused ? '♻ ' : '') + tag
									+ '<a href="/app/bom/' + b.bom_name + '" target="_blank"><b>' + b.bom_name + '</b></a> for ' + b.item;
							}).join('<br>');
							frappe.msgprint({ title: 'Consolidated BOM Created',
								message: lines + '<br><br>FG BOMs: ' + (result.fg_boms || []).length, indicator: 'green' });
						},
						error: function () { self.saving = false; },
					});
				});
			},

			_doCreateBOM() {
				var self = this;
				var active_ops = self.operations.filter(function (op) { return !op.exclude_from_bom; });
				var excluded_count = self.operations.length - active_ops.length;
				var missing_ws = active_ops.filter(function (op) { return !op.workstation; });
				var msg = 'Create <b>' + active_ops.length + ' SFG BOM(s)</b> + 1 FG BOM for <b>'
					+ self.selectedFG + '</b>?<br>Mfg Qty: <b>' + self.mfgQty + '</b>';
				if (excluded_count) {
					msg += '<br><span style="color:#6b7280">ℹ ' + excluded_count + ' operation(s) excluded from BOM chain.</span>';
				}
				if (missing_ws.length) {
					msg += '<br><span style="color:#b45309">⚠ ' + missing_ws.length + ' operation(s) without Workstation — those SFG BOMs will have no operations entry.</span>';
				}
				frappe.confirm(msg, function () {
					self.saving = true;
					frappe.call({
						method: API.createBomChain,
						args: {
							fg_item:          self.selectedFG,
							mfg_qty:          self.mfgQty,
							operations:       JSON.stringify(self.operations),
							extra_materials:  JSON.stringify(self.extraMaterials),
							is_default:       self.isDefault ? 1 : 0,
						},
						freeze: true,
						freeze_message: 'Creating BOM chain…',
						callback: function (r) {
							self.saving = false;
							if (!r.message) return;
							var result = r.message;
							var lines = (result.created_boms || []).map(function (b) {
								return (b.reused ? '♻ Reused: ' : '✓ Created: ')
									+ '<a href="/app/bom/' + b.bom_name + '" target="_blank"><b>' + b.bom_name + '</b></a> for ' + b.item;
							}).join('<br>');
							frappe.msgprint({ title: 'BOM Chain Created', message: lines + '<br><br>FG BOM: <a href="/app/bom/' + result.fg_bom + '" target="_blank"><b>' + result.fg_bom + '</b></a>', indicator: 'green' });
						},
						error: function () { self.saving = false; },
					});
				});
			},
		},

		template: `
<div class="bb-app">

  <!-- UOM datalist (shared) -->
  <datalist id="bb-uom-list">
    <option value="Nos"></option><option value="Sheets"></option><option value="KG"></option>
    <option value="M2"></option><option value="Meter"></option><option value="Ltr"></option>
    <option value="Pcs"></option><option value="Set"></option><option value="Roll"></option>
    <option value="Box"></option><option value="Unit"></option><option value="Gram"></option>
  </datalist>


  <!-- Header -->
  <div class="bb-hdr">
    <div class="bb-hdr-title"><span class="bb-icon">⚙</span> BOM Builder <span class="bb-sub">— SFG Chain</span></div>
    <div class="bb-hdr-meta" v-if="sourceName">
      <span class="bb-tag">{{ sourceType }}</span>
      <b>{{ sourceName }}</b>
      <span v-if="customer" style="color:#aac4e0"> — {{ customer }}</span>
    </div>
    <div style="margin-left:auto" v-if="sourceType">
      <a :href="'/app/' + (sourceType==='Sales Order'?'sales-order':(sourceType==='Cost Sheet'?'cost-sheet':'savinda-quotation')) + '/' + sourceName" class="bb-back-btn">← Back</a>
    </div>
  </div>

  <div v-if="error" class="bb-error">{{ error }}</div>
  <div v-if="loading" class="bb-loading">Loading…</div>

  <div v-else-if="fgItems.length" class="bb-main">

    <!-- FG + CB -->
    <div class="bb-card">
      <div class="bb-card-title">Select Finish Good</div>

      <!-- Global CB — when all FGs share one Calculation Breakdown -->
      <div class="bb-global-cb-row">
        <span class="bb-global-cb-lbl">🔗 Global CB <span class="bb-hint">(set once when all FGs share the same Calculation Breakdown)</span></span>
        <div style="display:flex;gap:6px;flex:1;max-width:480px">
          <input type="text" v-model="globalCB" class="bb-inp" style="flex:1" placeholder="Shared Calculation Breakdown name…" />
          <button class="bb-btn bb-btn-blue" @click="applyGlobalCB" :disabled="!globalCB" style="white-space:nowrap">▶ Apply to All</button>
          <button class="bb-btn bb-btn-sm" @click="pickCB" title="Search CBs">🔍</button>
        </div>
      </div>

      <!-- Multi-FG picker -->
      <div class="bb-row" style="padding-bottom:0;align-items:center">
        <button class="bb-btn bb-btn-blue" @click="openFGPicker">🎯 Select FGs</button>
        <div v-if="selectedFGs.length" style="display:flex;flex-wrap:wrap;gap:5px;align-items:center">
          <span class="bb-hint">Building:</span>
          <span v-for="fg in selectedFGs" :key="fg" class="bb-leg-out" style="font-size:11px">{{ fg }}</span>
          <span v-if="selectedFGs.length > 1" class="bb-count">{{ selectedFGs.length }} FGs → consolidated (shared common SFGs)</span>
        </div>
      </div>

      <div class="bb-row">
        <div class="bb-field" style="flex:2">
          <label>Finish Good (FG) <span class="bb-hint">— chain is loaded from this one</span></label>
          <select v-model="selectedFG" class="bb-inp bb-sel" @change="onFGDropdown">
            <option value="">— Select FG —</option>
            <option v-for="fg in fgItems" :key="fg.item_code" :value="fg.item_code">
              {{ fg.item_code }} — {{ fg.item_name }}
            </option>
          </select>
        </div>
        <div class="bb-field" style="flex:0.8">
          <label>Manufacturing Qty</label>
          <input type="number" v-model.number="mfgQty" min="1" class="bb-inp" />
        </div>
        <div class="bb-field" style="flex:1.5">
          <label>
            Calculation Breakdown
            <span v-if="!calcBreakdown && selectedFG" class="bb-warn-tag">⚠ not linked</span>
          </label>
          <div style="display:flex;gap:4px">
            <input type="text" v-model="calcBreakdown" class="bb-inp" style="flex:1" placeholder="Auto-filled or type CB name" />
            <button class="bb-btn bb-btn-sm" @click="pickCB">🔍</button>
          </div>
        </div>
        <div class="bb-field" style="flex:0;align-self:flex-end">
          <button :class="['bb-btn', qtyChanged ? 'bb-btn-recalc-alert' : 'bb-btn-blue']"
            @click="loadBomData(true)" :disabled="!calcBreakdown||!mfgQty"
            :title="qtyChanged ? 'Manufacturing qty changed — recalculate to update all quantities' : 'Recalculate all quantities'">
            {{ qtyChanged ? '⚠ Recalculate Qty' : '↻ Recalculate' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Sheet Requirements -->
    <div class="bb-card" v-if="selectedFG && sheetData && sheetData.full_sheet_qty">
      <div class="bb-card-title-row">
        <div class="bb-card-title">
          Sheet Requirements
          <span class="bb-hint" style="margin-left:6px">calculated at mfg qty {{ fmtN(mfgQty) }}</span>
        </div>
        <button class="bb-btn bb-btn-sm" @click="sheetExpanded = !sheetExpanded">{{ sheetExpanded ? '▴' : '▾' }}</button>
      </div>
      <div v-show="sheetExpanded" style="padding:12px 14px">
        <div class="bb-sheet-grid">
          <div class="bb-sheet-kv">
            <div class="bb-sheet-lbl">Cut Sheet Ups</div>
            <div class="bb-sheet-val">{{ sheetData.cut_sheet_ups || 0 }}</div>
          </div>
          <div class="bb-sheet-kv">
            <div class="bb-sheet-lbl">Cut Sheet Qty</div>
            <div class="bb-sheet-val">{{ fmtN(sheetData.cut_sheet_qty) }}</div>
          </div>
          <div class="bb-sheet-kv">
            <div class="bb-sheet-lbl">Wastage</div>
            <div class="bb-sheet-val">{{ fmtN(sheetData.wastage) }}</div>
          </div>
          <div class="bb-sheet-kv bb-sheet-kv-highlight">
            <div class="bb-sheet-lbl">Full Sheet Qty <span class="bb-sheet-tag">📄 Board input</span></div>
            <div class="bb-sheet-val bb-sheet-blue">{{ fmtN(sheetData.full_sheet_qty) }}</div>
          </div>
          <div class="bb-sheet-kv bb-sheet-kv-highlight">
            <div class="bb-sheet-lbl">Req. Cut Sheets <span class="bb-sheet-tag">✂ SFG flow qty</span></div>
            <div class="bb-sheet-val bb-sheet-blue">{{ fmtN(sheetData.req_cut_sheets) }}</div>
          </div>
        </div>
        <div class="bb-sheet-note">
          📄 <b>{{ baseMatName || baseMat }}</b> board → <b>{{ fmtN(sheetData.full_sheet_qty) }} {{ baseMatUom }}</b> into press
          &nbsp;→&nbsp;
          ✂ <b>{{ fmtN(sheetData.req_cut_sheets) }} cut sheets</b> flow through all SFG operations
          &nbsp;→&nbsp;
          🔀 split to <b>{{ fmtN(itemQty) }} items</b> at split point
        </div>
      </div>
    </div>

    <!-- SFG Operation Chain -->
    <div class="bb-card" v-if="selectedFG">
      <div class="bb-card-title-row">
        <div class="bb-card-title">
          SFG Operation Chain
          <span class="bb-count">{{ operations.length }} ops</span>
          <span class="bb-hint" style="margin-left:8px">Drag ≡ to reorder — chain updates automatically</span>
        </div>
      </div>

      <div v-if="!operations.length" class="bb-empty" style="padding:12px 14px">
        No operations. Click ↻ Load after selecting FG + CB.
      </div>

      <div v-else>
        <!-- Chain legend -->
        <div class="bb-chain-legend">
          <span class="bb-leg-in">▶ Input Material</span>
          <span style="color:#888;margin:0 8px">→</span>
          <span style="color:#555;font-weight:600">Operation</span>
          <span style="color:#888;margin:0 8px">→</span>
          <span class="bb-leg-out">SFG Output ▶</span>
          <button class="bb-btn-add-op" @click="addOperation" title="Add an operation / SFG that isn't in the costing breakdown">+ Add Operation</button>
        </div>

        <div v-for="(op, i) in operations" :key="i" :class="['bb-op-block', op.split_to_item_unit ? 'bb-split-block' : '', op.exclude_from_bom ? 'bb-op-excluded' : '']">
        <div v-if="op.exclude_from_bom" class="bb-op-excluded-banner">
          🚫 Excluded from BOM — this operation is skipped during BOM chain creation
        </div>
        <div v-if="op.split_to_item_unit" class="bb-split-banner">
          🔀 Split to Item Unit — output qty becomes <b>{{ (itemQty || mfgQty) | 0 }}</b> (order qty)
        </div>
        <div class="bb-op-row"
             draggable="true"
             @dragstart="onDragStart(i)"
             @dragover="onDragOver"
             @drop="onDrop(i)">

          <!-- Drag handle + seq -->
          <div class="bb-op-drag">
            <span class="bb-drag-handle">≡</span>
            <span class="bb-op-seq">{{ i + 1 }}</span>
          </div>

          <!-- Input Material -->
          <div class="bb-op-input">
            <div class="bb-op-label">Input Material</div>
            <div class="bb-op-item-badge bb-badge-in" :title="op.input_item_code">
              {{ op.input_item_name || op.input_item_code || '—' }}
            </div>
            <div class="bb-op-qty">
              <input type="number" v-model.number="op.input_qty" min="0" step="0.001" class="bb-inp bb-inp-xs" />
              <input type="text" v-model="op.input_uom" class="bb-inp bb-inp-uom" list="bb-uom-list" />
            </div>
          </div>

          <div class="bb-op-arrow">→</div>

          <!-- Operation -->
          <div class="bb-op-center">
            <div class="bb-op-spec">{{ op.spec_name }}</div>
            <div class="bb-op-machine">{{ op.machine }}</div>
            <div v-if="op.workstation" class="bb-ws-badge">{{ op.workstation }}</div>
            <div v-else class="bb-no-ws">⚠ no workstation</div>
            <!-- ERPNext Operation override -->
            <div class="bb-op-erp-row">
              <span style="font-size:9px;color:#888;flex-shrink:0">Op:</span>
              <input type="text" v-model="op.erp_operation" class="bb-inp"
                style="width:84px;padding:2px 5px;font-size:10px"
                :placeholder="op.spec_name.substring(0,12)" />
              <button class="bb-btn-link" @click="pickOperation(op)" title="Pick from ERPNext Operations">🔍</button>
            </div>
            <!-- Split to item unit button -->
            <button :class="['bb-split-btn', op.split_to_item_unit ? 'bb-split-on' : '']"
              @click="setSplitPoint(i)"
              :title="op.split_to_item_unit ? 'Click to remove split point' : 'Set as split-to-item-unit: qty changes to order qty after this op'">
              {{ op.split_to_item_unit ? '🔀 Split ✓' : '🔀 Split' }}
            </button>
            <!-- Exclude from BOM toggle -->
            <button :class="['bb-excl-btn', op.exclude_from_bom ? 'bb-excl-on' : '']"
              @click="op.exclude_from_bom = !op.exclude_from_bom"
              :title="op.exclude_from_bom ? 'Excluded — click to re-include in BOM chain' : 'Exclude this spec from BOM chain (e.g. Proof Board)'">
              {{ op.exclude_from_bom ? '🚫 Excluded' : '⊘ Exclude' }}
            </button>
            <!-- Final Common SFG point -->
            <button :class="['bb-split-btn', op.common_sfg_point ? 'bb-split-on' : '']"
              @click="setCommonSfg(i)"
              :title="op.common_sfg_point ? 'Click to clear' : 'Final Common SFG: SFGs up to here are shared across all FGs; after this they become per-FG'">
              {{ op.common_sfg_point ? '🧩 Common SFG ✓' : '🧩 Common SFG' }}
            </button>
            <!-- Output is FG -->
            <button :class="['bb-excl-btn', op.output_is_fg ? 'bb-split-on' : '']"
              @click="setOutputFg(i)"
              :title="op.output_is_fg ? 'This op produces the FG — click to clear' : 'Mark this op as producing the Finished Good (no separate final FG BOM)'">
              {{ op.output_is_fg ? '🏁 Output = FG ✓' : '🏁 Output = FG' }}
            </button>
            <!-- Quality Inspection -->
            <label class="bb-chk-lbl" style="font-size:10px;justify-content:center;margin-top:4px">
              <input type="checkbox" v-model="op.has_quality_inspection" style="margin-right:3px" />
              QI Required
            </label>
            <div v-if="op.has_quality_inspection" class="bb-op-erp-row" style="margin-top:2px">
              <input type="text" v-model="op.quality_inspection_template" class="bb-inp"
                style="width:84px;padding:2px 5px;font-size:10px" placeholder="QI Template" />
              <button class="bb-btn-link" @click="pickQITemplate(op)" title="Pick Quality Inspection Template">🔍</button>
            </div>
            <div class="bb-op-time">
              <input type="number" v-model.number="op.time_in_mins" min="0" step="1" class="bb-inp bb-inp-xs" />
              <span style="font-size:10px;color:#888">mins</span>
            </div>
          </div>

          <div class="bb-op-arrow">→</div>

          <!-- Output SFG -->
          <div class="bb-op-output">
            <div class="bb-op-label">Output SFG</div>
            <input type="text" v-model="op.sfg_code" class="bb-inp bb-inp-sm" placeholder="SFG item code"
              @blur="onSFGChange(op)" style="margin-bottom:4px" />
            <input type="text" v-model="op.sfg_name" class="bb-inp bb-inp-sm" placeholder="SFG item name"
              @blur="onSFGChange(op)" />
            <div class="bb-op-qty">
              <input type="number" v-model.number="op.output_qty" min="0" step="0.001" class="bb-inp bb-inp-xs" />
              <input type="text" v-model="op.output_uom" class="bb-inp bb-inp-uom" list="bb-uom-list" />
            </div>
          </div>

          <button class="bb-rm-btn" @click="removeOp(i)">×</button>
        </div><!-- /bb-op-row -->

        <!-- Per-operation raw materials — INSIDE the v-for block -->
        <div class="bb-op-mats" v-if="op.materials && op.materials.length > 0">
          <div class="bb-op-mats-title">
            <span>{{ op.is_print ? '🎨 Ink / Colors (' + op.no_of_colors + ')' : '📦 Materials for this operation' }}</span>
            <button class="bb-btn-link" @click="addOpMat(op)" style="font-size:10px">+ Add</button>
          </div>
          <div v-for="(mat, mi) in op.materials" :key="mi" class="bb-mat-row">
            <label class="bb-mat-chk" :title="mat.include ? 'Include in BOM' : 'Excluded from BOM'">
              <input type="checkbox" v-model="mat.include" style="margin-right:4px" />
            </label>
            <span v-if="mat.label" class="bb-color-label">{{ mat.label }}</span>
            <div class="bb-mat-item" @click="searchOpMatItem(mat)" :class="{'bb-mat-item-empty': !mat.item_code}">
              {{ mat.item_code ? (mat.item_name || mat.item_code) : '— Select item —' }}
            </div>
            <input type="number" v-model.number="mat.qty" min="0" step="0.000001" class="bb-inp bb-inp-xs" title="Quantity" />
            <input type="text" v-model="mat.uom" class="bb-inp bb-inp-uom" title="UOM" />
            <button class="bb-rm-btn" style="font-size:13px" @click="removeOpMat(op, mi)">×</button>
          </div>
        </div>
        <div class="bb-op-mats bb-op-mats-empty" v-else>
          <span class="bb-hint">No materials for this operation. </span>
          <button class="bb-btn-link" @click="addOpMat(op)">+ Add material</button>
        </div>
        </div><!-- /bb-op-block (closes v-for) -->

        <!-- Chain summary -->
        <div class="bb-chain-summary">
          <span class="bb-leg-in">Base: {{ baseMatName || baseMat || '?' }}</span>
          <span v-for="(op, i) in operations" :key="i">
            → <b>{{ op.spec_name }}</b> → {{ op.sfg_name || op.sfg_code }}
          </span>
          → <b class="bb-fg-badge">{{ selectedFG }}</b>
        </div>
      </div>
    </div>

    <!-- Materials are now embedded under each operation — no separate section needed -->

    <!-- Create BOM Chain -->
    <div class="bb-card bb-create-card" v-if="selectedFG">
      <div style="font-size:12px;color:#555;margin-bottom:10px">
        <template v-if="selectedFGs.length > 1">
          Consolidated build: <b>common SFGs</b> (shared) + per-FG tail + <b>{{ selectedFGs.length }} FG BOMs</b>.
        </template>
        <template v-else>
          Will create <b>{{ operations.length }} SFG BOM(s)</b> (skips existing active BOMs) + <b>1 FG BOM</b> for <b>{{ selectedFG }}</b>
        </template>
      </div>
      <label class="bb-chk-lbl" style="margin-bottom:10px">
        <input type="checkbox" v-model="isDefault" style="margin-right:6px" />
        Set FG BOM as Default
      </label>
      <div style="display:flex;gap:8px">
        <button class="bb-btn bb-btn-create" @click="createBOM" :disabled="!canCreate||saving" style="flex:1">
          {{ saving ? 'Creating…' : (selectedFGs.length > 1 ? ('⚙ Create Consolidated BOM for ' + selectedFGs.length + ' FGs') : ('⚙ Create BOM Chain for ' + selectedFG)) }}
        </button>
        <button class="bb-btn" style="background:#1e40af;color:#fff;padding:10px 16px;border:none;border-radius:5px;cursor:pointer;font-size:13px"
          @click="saveConfig" :disabled="!operations.length" title="Save this chain so it reloads next time">
          💾 Save Config
        </button>
      </div>
    </div>

  </div>

  <div v-else-if="!loading && sourceName" class="bb-empty-page">
    <div>No FG items found in <b>{{ sourceName }}</b>.</div>
    <div style="margin-top:8px;font-size:12px;color:#888">Create FG items from the Savinda Quotation first.</div>
  </div>

</div>
`,
	});

	var vm = app.mount(el);
	window.__bb_app__ = vm;
}

// ─────────────────────────────────────────────────────────────
function bb_inject_styles() {
	if (document.getElementById('bb-styles')) return;
	var s = document.createElement('style');
	s.id = 'bb-styles';
	s.textContent = `
.bb-page-wrapper .page-content-wrapper{padding:0}
.bb-app{font-family:var(--font-stack,-apple-system,sans-serif);font-size:13px;color:#222;max-width:1200px;margin:0 auto;padding:14px 18px}
.bb-hdr{display:flex;align-items:center;gap:12px;padding:10px 14px;background:#1a3a5c;color:#fff;border-radius:6px;margin-bottom:14px}
.bb-hdr-title{font-size:15px;font-weight:700;display:flex;align-items:center;gap:6px}
.bb-icon{font-size:17px}.bb-sub{font-weight:400;font-size:12px;opacity:.8}
.bb-hdr-meta{font-size:12px;color:#cdd9e8;display:flex;align-items:center;gap:6px}
.bb-tag{background:#2c5f8a;padding:1px 7px;border-radius:3px;font-size:10px;font-weight:600;text-transform:uppercase}
.bb-back-btn{color:#8bb8d8;font-size:12px;text-decoration:none;margin-left:auto}
.bb-back-btn:hover{color:#fff}
.bb-error{background:#fef2f2;border:1px solid #fca5a5;border-radius:5px;padding:10px 14px;color:#b91c1c;margin-bottom:12px}
.bb-loading{text-align:center;padding:30px;color:#888}
.bb-empty{color:#aaa;font-style:italic;font-size:12px}
.bb-empty-page{text-align:center;padding:40px;color:#555}
.bb-card{background:#fff;border:1px solid #e5e7eb;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.05);margin-bottom:14px;overflow:hidden}
.bb-card-title{font-size:11.5px;font-weight:700;color:#1a3a5c;text-transform:uppercase;letter-spacing:.05em;padding:9px 14px;border-bottom:1px solid #eef2f8;background:#f8faff}
.bb-card-title-row{display:flex;align-items:center;justify-content:space-between;padding:8px 14px;border-bottom:1px solid #eef2f8;background:#f8faff}
.bb-hint{font-size:10px;color:#888;font-style:italic;font-weight:400;text-transform:none;letter-spacing:0}
.bb-count{background:#1a3a5c;color:#fff;border-radius:10px;padding:1px 7px;font-size:10px;margin-left:5px;text-transform:none}
.bb-row{display:flex;gap:10px;padding:12px 14px;flex-wrap:wrap;align-items:flex-start}
.bb-field{display:flex;flex-direction:column;gap:4px}
.bb-field label{font-size:11px;font-weight:600;color:#555}
.bb-inp{padding:6px 8px;border:1px solid #d1d5db;border-radius:4px;font-size:12.5px;width:100%;box-sizing:border-box}
.bb-inp:focus{outline:none;border-color:#1a3a5c}
.bb-inp-sm{padding:4px 6px;font-size:12px}
.bb-inp-xs{width:70px!important;padding:3px 5px;font-size:11px}
.bb-inp-uom{width:50px!important;padding:3px 5px;font-size:11px}
.bb-sel{cursor:pointer}
.bb-warn-tag{background:#fef9c3;color:#854d0e;border-radius:3px;padding:1px 5px;font-size:10px;margin-left:4px;font-weight:400;text-transform:none;letter-spacing:0}
.bb-btn{padding:6px 12px;border:none;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600}
.bb-btn-blue{background:#1a3a5c;color:#fff}
.bb-btn-recalc-alert{background:#b45309;color:#fff;animation:bb-pulse 1.5s infinite}
.bb-btn-blue:hover:not(:disabled){background:#2c5f8a}
.bb-btn-blue:disabled{opacity:.5;cursor:not-allowed}
.bb-btn-sm{background:#f3f4f6;color:#374151;border:1px solid #d1d5db;padding:4px 8px}
.bb-btn-sm:hover{background:#e5e7eb}
.bb-btn-create{background:#166534;color:#fff;padding:10px 24px;font-size:14px;width:100%;border-radius:5px;border:none;cursor:pointer;font-weight:700}
.bb-btn-create:hover:not(:disabled){background:#14532d}
.bb-btn-create:disabled{background:#9ca3af;cursor:not-allowed}
.bb-create-card{padding:16px 14px}
.bb-chk-lbl{display:flex;align-items:center;font-size:13px;cursor:pointer}
.bb-rm-btn{background:transparent;border:none;color:#dc2626;font-size:18px;cursor:pointer;padding:0 4px;line-height:1;flex-shrink:0}
.bb-tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.bb-tbl th{background:#1a3a5c;color:#fff;padding:7px 10px;text-align:left;font-size:11px;font-weight:600}
.bb-tbl td{padding:5px 10px;border-bottom:1px solid #f0f0f0;vertical-align:middle}
.bb-dim{color:#666;font-size:12px}
/* Operation chain */
.bb-chain-legend{display:flex;align-items:center;padding:8px 14px;background:#f0f4ff;font-size:11px;color:#555}
.bb-btn-add-op{margin-left:auto;border:1px solid #2c7be5;background:#2c7be5;color:#fff;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:600}
.bb-btn-add-op:hover{background:#1a5fc4}
.bb-leg-in{background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:3px;font-weight:600}
.bb-leg-out{background:#dcfce7;color:#166534;padding:2px 8px;border-radius:3px;font-weight:600}
/* Sheet requirements */
.bb-sheet-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:12px}
.bb-sheet-kv{background:#f8faff;border:1px solid #e5e7eb;border-radius:5px;padding:8px 12px}
.bb-sheet-kv-highlight{background:#f0f4ff;border-color:#c7d7f0}
.bb-sheet-lbl{font-size:10.5px;color:#888;margin-bottom:4px;font-weight:600;text-transform:uppercase;letter-spacing:.03em}
.bb-sheet-val{font-size:15px;font-weight:700;color:#1a3a5c;font-family:monospace}
.bb-sheet-blue{color:#1d4ed8}
.bb-sheet-tag{background:#dbeafe;color:#1e40af;border-radius:3px;padding:1px 5px;font-size:9px;font-weight:600;text-transform:none;letter-spacing:0;margin-left:4px}
.bb-sheet-note{font-size:11.5px;color:#555;background:#fafafa;border-radius:4px;padding:7px 10px;line-height:1.6}
.bb-op-block{border-bottom:2px solid #e5e7eb}
@keyframes bb-pulse{0%,100%{opacity:1}50%{opacity:.7}}
.bb-split-block{border-color:#7c3aed;border-width:2px}
.bb-split-banner{background:#ede9fe;color:#5b21b6;font-size:11px;font-weight:600;padding:4px 14px;border-bottom:1px solid #c4b5fd}
.bb-split-btn{border:1px solid #9ca3af;background:#f9fafb;color:#374151;padding:3px 7px;border-radius:4px;cursor:pointer;font-size:10px;margin-top:4px}
.bb-split-on{background:#7c3aed!important;color:#fff!important;border-color:#7c3aed!important}
.bb-op-row{display:flex;align-items:flex-start;gap:8px;padding:10px 14px;cursor:grab;transition:background .1s}
.bb-op-row:hover{background:#fafbff}
.bb-op-drag{display:flex;flex-direction:column;align-items:center;gap:2px;padding-top:4px;min-width:24px}
.bb-drag-handle{font-size:16px;color:#9ca3af;line-height:1}
.bb-op-seq{font-size:10px;font-weight:700;color:#9ca3af}
.bb-op-input,.bb-op-output{display:flex;flex-direction:column;gap:4px;flex:1;min-width:160px}
.bb-op-center{display:flex;flex-direction:column;gap:4px;min-width:130px;align-items:center;padding:0 4px}
.bb-op-label{font-size:9.5px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.04em}
.bb-op-item-badge{font-size:11px;font-weight:600;padding:3px 8px;border-radius:3px;word-break:break-word}
.bb-badge-in{background:#dbeafe;color:#1e40af}
.bb-op-spec{font-size:13px;font-weight:700;color:#1a3a5c;text-align:center}
.bb-op-machine{font-size:11px;color:#666;text-align:center}
.bb-op-time{display:flex;align-items:center;gap:4px;margin-top:4px}
.bb-op-qty{display:flex;align-items:center;gap:4px;margin-top:4px}
.bb-op-arrow{font-size:20px;color:#9ca3af;padding-top:30px;flex-shrink:0}
.bb-ws-badge{background:#dcfce7;color:#166534;padding:2px 7px;border-radius:3px;font-size:10.5px;font-weight:600}
.bb-no-ws{color:#dc2626;font-size:10.5px}
.bb-chain-summary{padding:8px 14px 12px;font-size:11px;color:#555;background:#f8faff;display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.bb-fg-badge{color:#166534;font-size:12px}
/* Global CB row */
.bb-global-cb-row{display:flex;align-items:center;gap:10px;padding:8px 14px;background:#f0f4ff;border-bottom:1px solid #dde4f0;flex-wrap:wrap}
.bb-global-cb-lbl{font-size:12px;font-weight:600;color:#1a3a5c;white-space:nowrap}
/* Operation materials */
.bb-op-mats{background:#fefce8;border-top:1px solid #fef08a;padding:8px 14px 10px 56px}
.bb-op-mats-empty{background:#f9fafb;border-top:1px solid #f0f0f0;padding:6px 14px 6px 56px}
.bb-op-mats-title{font-size:10.5px;font-weight:700;color:#92400e;margin-bottom:6px;display:flex;align-items:center;justify-content:space-between}
.bb-mat-row{display:flex;align-items:center;gap:6px;margin-bottom:4px}
.bb-mat-chk{display:flex;align-items:center;flex-shrink:0}
.bb-color-label{background:#7c3aed;color:#fff;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:700;white-space:nowrap;flex-shrink:0}
.bb-mat-item{flex:1;padding:4px 8px;background:#fff;border:1px solid #d1d5db;border-radius:4px;cursor:pointer;font-size:12px;min-width:120px}
.bb-mat-item:hover{border-color:#1a3a5c;background:#f0f4ff}
.bb-mat-item-empty{color:#9ca3af;font-style:italic}
/* Exclude from BOM */
.bb-excl-btn{border:1px solid #d1d5db;background:#f9fafb;color:#6b7280;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:10px;margin-top:4px;font-weight:600}
.bb-excl-btn:hover{background:#fee2e2;border-color:#fca5a5;color:#dc2626}
.bb-excl-on{background:#fee2e2!important;color:#dc2626!important;border-color:#fca5a5!important}
.bb-op-excluded .bb-op-row{opacity:0.45;background:#f9fafb}
.bb-op-excluded .bb-op-mats,.bb-op-excluded .bb-op-mats-empty{opacity:0.4}
.bb-op-excluded-banner{background:#fee2e2;color:#991b1b;font-size:11px;font-weight:600;padding:4px 14px;border-bottom:1px solid #fca5a5}
/* ERPNext Operation / QI row */
.bb-op-erp-row{display:flex;align-items:center;gap:3px;margin-top:3px;width:100%}
.bb-btn-link{background:none;border:none;cursor:pointer;color:#1a3a5c;font-size:13px;padding:0 2px;line-height:1;flex-shrink:0}
.bb-btn-link:hover{color:#2c5f8a}
`;
	document.head.appendChild(s);
}
