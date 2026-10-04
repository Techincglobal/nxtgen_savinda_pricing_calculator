// Shared helpers for the FG-creation popups (Cost Sheet + Savinda Quotation).
// Both render the same reviewable Product Library fields, whose schema (labels,
// fieldtypes, options, offset/flexo scope) is supplied by the server so the field
// list lives in one place (production_plan._pl_field_schema).
window.nxtgen_pl = window.nxtgen_pl || {};

// Build frappe.ui.Dialog field defs for the Product Library review fields.
//   pl_fields — [{fieldname,label,fieldtype,options,only}] from the server
//   is_flexo  — hide the fields scoped to the other pricing type
//   prefix    — namespaces the fieldnames (e.g. "l0__") for multi-item dialogs
//   line      — object holding pre-filled defaults keyed "pl_<fieldname>"
nxtgen_pl.fields = function (pl_fields, is_flexo, prefix, line) {
	prefix = prefix || "";
	line = line || {};
	var scope = is_flexo ? "Flexo" : "Offset";
	// Fields visible for this pricing type (drop the ones scoped to the other type).
	var defs = (pl_fields || [])
		.filter(function (f) { return f.fieldname !== "finishings" && (!f.only || f.only === scope); })
		.map(function (f) {
			return {
				fieldtype: f.fieldtype,
				fieldname: prefix + "pl_" + f.fieldname,
				label: __(f.label),
				options: f.options || undefined,
				default: line["pl_" + f.fieldname],
			};
		});
	// Lay them out as a compact 3–4 column grid (Column Breaks) instead of one tall column.
	var cols = defs.length > 12 ? 4 : (defs.length > 4 ? 3 : 1);
	var per_col = Math.ceil(defs.length / cols) || 1;
	var out = [];
	defs.forEach(function (def, i) {
		if (i > 0 && i % per_col === 0) { out.push({ fieldtype: "Column Break" }); }
		out.push(def);
	});
	if ((pl_fields || []).some(function (f) { return f.fieldname === "finishings"; })) {
		out.push({ fieldtype: "Section Break", label: __("Finishings") });
		out.push({
			fieldtype: "Table", fieldname: prefix + "pl_finishings", label: __("Finishings"),
			cannot_add_rows: false, cannot_delete_rows: false, in_place_edit: true,
			data: (line.pl_finishings || []).map(function (row) { return Object.assign({}, row); }),
			fields: [
				{ fieldtype: "Link", fieldname: "process_name", label: __("Finishing"), options: "Finishing", in_list_view: 1, reqd: 1, columns: 5 },
				{ fieldtype: "Data", fieldname: "machine_name", label: __("Machine"), in_list_view: 1, columns: 2 },
				{ fieldtype: "Data", fieldname: "remarks", label: __("Remarks"), in_list_view: 1, columns: 3 },
			],
			description: __("Loaded from the cost calculation. Add or remove finishings for this FG."),
		});
	}
	return out;
};

// Collect the edited PL values back into { fieldname: value }, dropping blanks so
// they never clobber a computed default on the server.
nxtgen_pl.overrides = function (pl_fields, values, prefix) {
	prefix = prefix || "";
	var out = {};
	(pl_fields || []).forEach(function (f) {
		var v = values[prefix + "pl_" + f.fieldname];
		if (f.fieldname === "finishings") {
			out.finishings = v || [];
			return;
		}
		if (v !== undefined && v !== null && v !== "") {
			out[f.fieldname] = v;
		}
	});
	return out;
};
