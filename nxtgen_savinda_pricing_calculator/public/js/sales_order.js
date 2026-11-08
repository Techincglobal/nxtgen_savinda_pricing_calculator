// Copyright (c) 2026, Techincglobal.com
// Sales Order → BOM Builder + Create Production Plan (Job Ticket).

frappe.ui.form.on("Sales Order", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 0 || frm.doc.docstatus === 1) {
			frm.add_custom_button(__("BOM Builder"), function () {
				window.location.href = "/app/bom-builder?so=" + encodeURIComponent(frm.doc.name);
			}, __("Manufacturing"));

			frm.add_custom_button(__("Create Production Plan"), function () {
				if (frm.is_dirty()) { frappe.msgprint(__("Save the Sales Order before checking its Production Plans.")); return; }
				open_sales_order_planning_dialog(frm);
			}, __("Manufacturing"));
		}


		frm.set_query("custom_sales_person", function () {
			return {
				filters: {
					department: "Marketing - SGSPL",
					status: "Active"
				}
			};
		});
		frm.set_query("custom_cs_person", function () {
			return {
				filters: {
					department: "CS - SGSPL",
					status: "Active"
				}
			};
		});
		frm.remove_custom_button('Quotation', 'Get items from');
		// Delivery Notes are raised only from the Delivery Note's "Get from Packing" flow, so
		// drop the direct "Create > Delivery Note" option on the Sales Order.
		frm.remove_custom_button('Delivery Note', 'Create');
	},
});

// Selections belong to Sales Order row IDs, not item codes (duplicate products are valid).
function open_sales_order_planning_dialog(frm) {
	const api = "nxtgen_savinda_pricing_calculator.api.production_plan.";
	const esc = value => frappe.utils.escape_html(String(value == null ? "" : value));
	const links = names => names.map(name => '<a href="/app/production-plan/' + encodeURIComponent(name) + '">' + esc(name) + '</a>').join('<br>');
	frappe.call({method: api + "get_sales_order_planning_items", args: {sales_order: frm.doc.name}, freeze: true,
		callback(r) {
			const data = r.message || {}, items = data.items || [];
			if (!items.length) {
				frappe.msgprint({title: __("Production Plans Already Exist"), message: __("No unplanned quantities remain.") + '<br>' + links(data.existing_production_plans || [])});
				return;
			}
			const selected = new Set();
			let busy = false;
			let d;
			d = new frappe.ui.Dialog({title: __("Select Items for Production Planning"), size: "extra-large",
				fields: [
					{fieldname: "mode", fieldtype: "Select", label: __("Create Production Plans"), reqd: 1,
						options: ["One PP for selected items", "Individual PP for each selected item"], default: "One PP for selected items"},
					{fieldname: "gang", fieldtype: "Select", label: __("Filter by Gang ID"), options: "All available items", onchange: () => render()},
					{fieldname: "selection_count", fieldtype: "HTML"},
					{fieldname: "items_html", fieldtype: "HTML"}
				], primary_action_label: __("Create Production Plans"),
				primary_action() {
					if (busy) return;
					const selections = items.filter(row => selected.has(row.sales_order_item)).map(row => ({sales_order_item: row.sales_order_item, qty: row.qty}));
					if (!selections.length) { frappe.msgprint(__("Select at least one available item.")); return; }
					busy = true; d.get_primary_btn().prop("disabled", true);
					frappe.call({method: api + "create_plan_from_sales_order", args: {sales_order: frm.doc.name,
						selections: JSON.stringify(selections), plan_mode: d.get_value("mode") === "Individual PP for each selected item" ? "individual" : "combined"},
						freeze: true, freeze_message: __("Creating Production Plans…"),
						callback(result) {
							const names = (result.message || {}).production_plans || [];
							d.hide();
							if (names.length === 1) frappe.set_route("Form", "Production Plan", names[0]);
							else frappe.msgprint({title: __("Production Plans Created"), message: links(names), indicator: "green"});
						}, always() { busy = false; d.get_primary_btn().prop("disabled", false); }
					});
				}
			});
			// DOM option values avoid collisions between an actual Gang ID and filter labels.
			const gangSelect = d.fields_dict.gang.$input;
			gangSelect.empty().append($('<option>').val("").text(__("All available items")));
			(data.gang_ids || []).forEach((id, i) => gangSelect.append($('<option>').val(String(i)).text(id)));
			if (items.some(row => !row.gang_id)) gangSelect.append($('<option>').val("ungrouped").text(__("No Gang ID")));
			gangSelect.val("");
			function render() {
				if (!d) return;
				const filter = d.fields_dict.gang.$input.val() || "";
				const visible = items.filter(row => !filter || (filter === "ungrouped" ? !row.gang_id : row.gang_id === data.gang_ids[Number(filter)]));
				const body = visible.map(row => '<tr><td><input type="checkbox" class="pp-item" data-row="' + esc(row.sales_order_item) + '" ' + (selected.has(row.sales_order_item) ? 'checked' : '') + '></td><td>' + esc(row.item_code) + '</td><td>' + esc(row.item_name) + '</td><td>' + esc(row.gang_id) + '</td><td>' + esc(row.order_qty) + '</td><td>' + esc(row.planned_qty) + '</td><td>' + esc(row.qty) + '</td><td>' + esc(row.uom) + '</td></tr>').join("");
				const wrapper = d.fields_dict.items_html.$wrapper;
				wrapper.html('<table class="table table-bordered"><thead><tr><th><input type="checkbox" class="pp-all"></th><th>Item Code</th><th>Item Name</th><th>Gang ID</th><th>Order Qty</th><th>Already Planned</th><th>Available Qty</th><th>UOM</th></tr></thead><tbody>' + body + '</tbody></table>');
				wrapper.find('.pp-all').prop('checked', visible.length > 0 && visible.every(row => selected.has(row.sales_order_item))).on('change', function () {
					visible.forEach(row => this.checked ? selected.add(row.sales_order_item) : selected.delete(row.sales_order_item)); render();
				});
				wrapper.find('.pp-item').on('change', function () {
					const id = $(this).attr('data-row'); this.checked ? selected.add(id) : selected.delete(id); render();
				});
				d.fields_dict.selection_count.$wrapper.text(__("{0} item(s) selected across all filters. Selected rows use their available quantities.", [selected.size]));
			}
			d.show(); render();
		}
	});
}
