"""Repair the 'Offset Job Ticket' print format.

The DB record had drifted (it was saved as standard="No", so `bench migrate` never refreshed
it from the app file). That stale copy held a mislabeled FLEXO layout, was unbound (read
po_items / blank cells), and crashed on render — `frappe.get_doc("Quotation", quote_no)` on a
Savinda Quotation raised DoesNotExistError.

The app ships the correct, data-bound Offset layout as a standard file. Force-import it so the
DB matches; because the file is standard="Yes", future migrates keep it in sync.
"""

import frappe


def execute():
	from frappe.modules.import_file import import_file_by_path

	path = frappe.get_app_path(
		"nxtgen_savinda_pricing_calculator",
		"nxtgen_savinda_pricing_calculator",
		"print_format",
		"offset_job_ticket",
		"offset_job_ticket.json",
	)
	try:
		import_file_by_path(path, force=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="offset_job_ticket_bind patch failed",
			message=frappe.get_traceback(),
		)
