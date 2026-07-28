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
	var out = [];
	(pl_fields || []).forEach(function (f) {
		if (f.only && f.only !== scope) return;
		out.push({
			fieldtype: f.fieldtype,
			fieldname: prefix + "pl_" + f.fieldname,
			label: __(f.label),
			options: f.options || undefined,
			default: line["pl_" + f.fieldname],
		});
	});
	return out;
};

// Collect the edited PL values back into { fieldname: value }, dropping blanks so
// they never clobber a computed default on the server.
nxtgen_pl.overrides = function (pl_fields, values, prefix) {
	prefix = prefix || "";
	var out = {};
	(pl_fields || []).forEach(function (f) {
		var v = values[prefix + "pl_" + f.fieldname];
		if (v !== undefined && v !== null && v !== "") {
			out[f.fieldname] = v;
		}
	});
	return out;
};
