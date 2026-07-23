# Copyright (c) 2026, Techincglobal.com and contributors
import frappe
from frappe.model.document import Document
from frappe.utils import now

ARTWORK_APPROVER_ROLE = "Artwork Approver"


class NPDRequest(Document):

	def before_insert(self):
		if not self.created_by_name:
			self.created_by_name = (
				frappe.db.get_value("User", frappe.session.user, "full_name")
				or frappe.session.user
			)
		if not self.created_on:
			self.created_on = now()

	def validate(self):
		if self.customer and not self.customer_name:
			self.customer_name = (
				frappe.db.get_value("Customer", self.customer, "customer_name")
				or self.customer_name
			)
		self.status = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(self.docstatus, "Draft")

	def before_submit(self):
		# Artwork must be approved before the NPD Request can be submitted (same gate as Cost Sheet).
		if (self.artwork_status or "Pending") != "Approved":
			frappe.throw(
				"Artwork must be <b>Approved</b> before submitting this NPD Request "
				"(current status: <b>{0}</b>). Ask an Artwork Approver to approve it.".format(
					self.artwork_status or "Pending"
				)
			)


# ── Artwork approval (cloned from Cost Sheet) ───────────────────────────────

def _check_artwork_approver():
	roles = frappe.get_roles(frappe.session.user)
	if ARTWORK_APPROVER_ROLE not in roles and "System Manager" not in roles:
		frappe.throw(
			"Only users with the '" + ARTWORK_APPROVER_ROLE + "' role can approve or reject artwork."
		)


def _set_artwork_status(npd_request, status, remarks=None):
	_check_artwork_approver()
	doc = frappe.get_doc("NPD Request", npd_request)
	doc.artwork_status = status
	doc.artwork_approved_by = frappe.session.user
	doc.artwork_approved_on = now()
	if remarks is not None:
		doc.artwork_remarks = remarks
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	return {"ok": True, "status": status}


@frappe.whitelist()
def approve_artwork(npd_request, remarks=None):
	return _set_artwork_status(npd_request, "Approved", remarks)


@frappe.whitelist()
def reject_artwork(npd_request, remarks=None):
	return _set_artwork_status(npd_request, "Rejected", remarks)
