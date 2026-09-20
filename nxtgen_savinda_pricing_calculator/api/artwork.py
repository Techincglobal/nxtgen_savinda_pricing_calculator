"""Independent artwork validation held on the FG's Product Library record."""
import frappe
from frappe.utils import now


@frappe.whitelist()
def validate_fg_artwork(fg_item, status, remarks=None):
	roles = frappe.get_roles(frappe.session.user)
	if "Supply Chain" not in roles and "System Manager" not in roles:
		frappe.throw("Only Supply Chain users can validate FG artwork.")
	if status not in ("Approved", "Rejected"):
		frappe.throw("Select Approved or Rejected.")
	pl = frappe.db.get_value("Item", fg_item, "custom_product_library")
	if not pl or not frappe.db.exists("Product Library", pl):
		frappe.throw("This FG has no linked Product Library record.")
	frappe.db.set_value("Product Library", pl, {
		"artwork_validation_status": status,
		"artwork_validation_remarks": (remarks or "").strip(),
		"artwork_validated_by": frappe.session.user,
		"artwork_validated_on": now(),
	})
	return {"product_library": pl, "status": status}
