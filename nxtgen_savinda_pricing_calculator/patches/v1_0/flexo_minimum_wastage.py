import frappe
from frappe.utils import cint


def execute():
	"""Initialize only the new minimum; keep existing setup and percentage schedules."""
	doc = frappe.get_single("Costing Configuration")
	rows = sorted(doc.get("flexo_wastage") or [], key=lambda r: cint(r.max_colors))
	previous = -1
	for row in rows:
		upper = cint(row.max_colors)
		# Split a broad existing band at new minimum boundaries, without changing its rates.
		for boundary in (0, 2):
			if previous < boundary < upper:
				doc.append("flexo_wastage", dict(max_colors=boundary,
					setup_metrage=row.setup_metrage, wastage_percent=row.wastage_percent,
					minimum_wastage_length=0 if boundary == 0 else 100))
		row.minimum_wastage_length = 0 if upper == 0 else 100 if upper <= 2 else 250
		previous = upper
	if rows:
		# A low last threshold is also the fallback for all higher colour counts.
		if previous < 3:
			last = rows[-1]
			if previous < 2:
				doc.append("flexo_wastage", dict(max_colors=2, setup_metrage=last.setup_metrage,
					wastage_percent=last.wastage_percent, minimum_wastage_length=100))
			doc.append("flexo_wastage", dict(max_colors=99, setup_metrage=last.setup_metrage,
				wastage_percent=last.wastage_percent, minimum_wastage_length=250))
		doc.flexo_wastage.sort(key=lambda r: cint(r.max_colors))
		for idx, row in enumerate(doc.flexo_wastage, 1):
			row.idx = idx
		doc.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Costing Configuration")
