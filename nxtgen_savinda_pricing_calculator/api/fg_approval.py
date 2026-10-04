"""Approval applies only to FGs explicitly created through the pricing APIs."""
from contextlib import contextmanager

import frappe
from frappe.utils import cint, flt, get_url_to_form, now_datetime, today

_TOKEN = object()
PENDING = "Pending Validation"
RETURNED = "Returned for Correction"
APPROVED = "Approved"
PROTECTED = ("approval_required", "approval_status", "approved_by", "approved_on", "approval_remarks", "fg_item")


@contextmanager
def _authorized_change():
	previous = getattr(frappe.local, "fg_approval_token", None)
	frappe.local.fg_approval_token = _TOKEN
	try:
		yield
	finally:
		frappe.local.fg_approval_token = previous


def _authorized():
	return getattr(frappe.local, "fg_approval_token", None) is _TOKEN


def _settings():
	return frappe.get_single("FG Approval Settings")


def _has_role(role):
	return frappe.session.user == "Administrator" or role in frappe.get_roles()


def _can_review(settings):
	return _has_role(settings.validator_role) or _has_role(settings.approver_role)


def ensure_review_permissions(settings):
	"""Grant configured reviewers PL access, without changing Item permissions."""
	from frappe.permissions import add_permission, update_permission_property
	for role in {settings.validator_role, settings.approver_role} - {None, ""}:
		if not frappe.db.exists("Custom DocPerm", {"parent": "Product Library", "role": role, "permlevel": 0, "if_owner": 0}):
			add_permission("Product Library", role)
		for permission in ("read", "write", "report"):
			update_permission_property("Product Library", role, 0, permission, 1, validate=False)
	frappe.clear_cache(doctype="Product Library")


def setup_fg_approval():
	"""Install missing defaults only: never overwrite a site's roles/templates."""
	settings = _settings()
	changed = False
	for event, status in (("pending", PENDING), ("returned", RETURNED), ("approved", APPROVED)):
		name = "Pricing FG " + status
		if not frappe.db.exists("Email Template", name):
			frappe.get_doc({
				"doctype": "Email Template", "name": name, "subject": status + ": {{ fg_item }}",
				"use_html": 1,
				"response_html": "<p>FG <b>{{ fg_item | e }}</b>: " + status + ".</p>"
					"<p>Product: {{ doc.product_name | e }}<br>Customer: {{ doc.customer | default('', true) | e }}"
					"<br>Cost Item: {{ doc.cost_item | default('', true) | e }}</p>"
					"<p>{{ remarks | default('', true) | e }}</p>"
					"<p><a href='{{ product_library_url | e }}'>Open Product Library</a></p>",
			}).insert(ignore_permissions=True)
		if not settings.get(event + "_template"):
			settings.set(event + "_template", name)
			changed = True
	if changed:
		settings.save(ignore_permissions=True)
	else:
		ensure_review_permissions(settings)


def insert_pending_fg(item, cost_item, customer_ref, overrides):
	from nxtgen_savinda_pricing_calculator.api.manufacturing import _upsert_product_library
	item.disabled = 1
	item.custom_fg_approval_required = 1
	with _authorized_change():
		item.insert(ignore_permissions=True)
		pl_name = _upsert_product_library(item.name, cost_item, customer_ref, overrides, require_approval=True)
		if not pl_name:
			frappe.throw("Product Library creation failed. The FG was not created.")
	return pl_name


def validate_item_approval(doc, method=None):
	old = doc.get_doc_before_save()
	if not (doc.get("custom_fg_approval_required") or (old and old.get("custom_fg_approval_required"))):
		return  # Standard Items, raw materials and legacy FGs are completely unaffected.
	if old and (not doc.get("custom_fg_approval_required") or doc.get("custom_product_library") != old.get("custom_product_library")):
		frappe.throw("The pricing FG approval link/requirement cannot be changed manually.")
	if doc.is_new() and not _authorized():
		frappe.throw("Pricing FG approval is assigned only by the pricing-app FG creation process.")
	if not cint(doc.disabled):
		pl = frappe.db.get_value("Product Library", doc.get("custom_product_library"), ["approval_required", "approval_status", "fg_item"], as_dict=True) if doc.get("custom_product_library") else None
		if not pl or not pl.approval_required or pl.fg_item != doc.name or pl.approval_status != APPROVED:
			frappe.throw("Validate Product Library and pricing, then use Approve & Enable FG. This FG must remain disabled until approval.")


def validate_product_library(doc):
	old = doc.get_doc_before_save()
	if not (doc.get("approval_required") or (old and old.get("approval_required"))):
		return
	if not _authorized():
		if doc.is_new() or any(doc.has_value_changed(key) for key in PROTECTED):
			frappe.throw("Use the Product Library approval actions to change FG approval details.")


def on_product_library_update(doc):
	if not doc.get("approval_required"):
		return
	old = doc.get_doc_before_save()
	if old and old.approval_status == doc.approval_status:
		return
	event = {PENDING: "pending", RETURNED: "returned", APPROVED: "approved"}.get(doc.approval_status)
	if event:
		frappe.enqueue(
			"nxtgen_savinda_pricing_calculator.api.fg_approval.notify_review",
			enqueue_after_commit=True, product_library=doc.name, event=event,
			actor=frappe.session.user, remarks=doc.approval_remarks or "",
		)


def notify_review(product_library, event, actor, remarks=""):
	"""Runs after commit; notification/email failures cannot orphan a created FG."""
	settings, doc = _settings(), frappe.get_doc("Product Library", product_library)
	if not settings.send_system_notification and not settings.send_email:
		return
	if event == "pending":
		users = frappe.get_all("Has Role", filters={"parenttype": "User", "role": ["in", list({settings.validator_role, settings.approver_role})]}, pluck="parent")
	else:
		users = [doc.owner]
	users = frappe.get_all("User", filters={"name": ["in", list(set(users)) or [""]], "enabled": 1, "user_type": "System User"}, fields=["name", "email"])
	if not users:
		frappe.log_error(title="FG Approval: no enabled recipients", message=f"Product Library {doc.name}, event {event}. Check FG Approval Settings and user roles.")
		return
	context = {"doc": doc, "fg_item": doc.fg_item, "product_library_url": get_url_to_form("Product Library", doc.name), "actor": actor, "remarks": remarks}
	template = frappe.get_doc("Email Template", settings.get(event + "_template"))
	subject = frappe.render_template(template.subject, context)
	message = frappe.render_template(template.response_html if template.use_html else template.response, context)
	if settings.send_system_notification:
		for user in users:
			frappe.get_doc({"doctype": "Notification Log", "type": "Alert", "for_user": user.name,
				"subject": subject, "email_content": message, "document_type": "Product Library",
				"document_name": doc.name, "from_user": actor}).insert(ignore_permissions=True)
	if settings.send_email:
		try:
			frappe.sendmail(recipients=[u.email for u in users if u.email], subject=subject, message=message,
				reference_doctype="Product Library", reference_name=doc.name)
		except frappe.OutgoingEmailError:
			# Keep the in-system alerts even when outgoing email is not configured.
			frappe.log_error(title="FG Approval email failed", message=frappe.get_traceback())


@frappe.whitelist()
def get_review_actions(product_library):
	doc = frappe.get_doc("Product Library", product_library)
	doc.check_permission("read")
	settings = _settings()
	return {"review": _can_review(settings), "approve": _has_role(settings.approver_role),
		"resend": doc.owner == frappe.session.user or _can_review(settings) or _has_role("CS Team")}


def check_pricing_for_approval(doc):
	from nxtgen_savinda_pricing_calculator.api.pricing_rule import _active_auto_rule, _tier_qtys_for_cost_item
	qtys = _tier_qtys_for_cost_item(doc.cost_item) if doc.cost_item else []
	if not qtys:
		frappe.throw("The linked Cost Item has no order quantities to validate pricing against.")
	missing = [qty for qty in qtys if not (rule := _active_auto_rule(doc.fg_item, qty, customer=doc.customer, transaction_date=today())) or flt(rule.rate) <= 0]
	if missing:
		frappe.throw("Set active, positive-rate pricing rules for this FG/customer covering these costing quantities before approval: " + ", ".join(str(q) for q in missing))


@frappe.whitelist()
def review_fg(product_library, action, remarks=None):
	doc = frappe.get_doc("Product Library", product_library)
	doc.check_permission("write")
	if not doc.approval_required:
		frappe.throw("This Product Library does not require FG approval.")
	settings = _settings()
	if action in ("approve", "return"):
		allowed = _has_role(settings.approver_role) if action == "approve" else _can_review(settings)
		if not allowed:
			frappe.throw("Your role cannot perform this FG review action.", frappe.PermissionError)
		if doc.approval_status != PENDING:
			frappe.throw("Only a Pending Validation FG can be reviewed.")
		if action == "approve":
			for key in ("fg_item", "product_name", "department", "cost_item"):
				if not doc.get(key):
					frappe.throw(f"Complete {frappe.get_meta('Product Library').get_label(key)} before approval.")
			check_pricing_for_approval(doc)
			doc.approval_status, doc.approved_by, doc.approved_on = APPROVED, frappe.session.user, now_datetime()
		else:
			if not (remarks or "").strip():
				frappe.throw("Enter the corrections needed before returning the FG.")
			doc.approval_status = RETURNED
	elif action == "resend":
		if not (doc.owner == frappe.session.user or _can_review(settings) or _has_role("CS Team")):
			frappe.throw("Only the creator, CS Team or validation team can resend this FG.", frappe.PermissionError)
		if doc.approval_status != RETURNED:
			frappe.throw("Only a Returned for Correction FG can be resent.")
		doc.approval_status = PENDING
	else:
		frappe.throw("Unknown FG approval action.")
	doc.approval_remarks = (remarks or "").strip()
	with _authorized_change():
		doc.save()
		if action == "approve":
			item = frappe.get_doc("Item", doc.fg_item)
			if not item.get("custom_fg_approval_required") or item.custom_product_library != doc.name:
				frappe.throw("FG approval links do not match this Product Library.")
			item.disabled = 0
			item.save(ignore_permissions=True)
	doc.add_comment("Info", f"FG review: {action}. {doc.approval_remarks}")
	return {"status": doc.approval_status, "fg_item": doc.fg_item}


def require_pricing_reviewer(fg_item):
	if frappe.db.get_value("Item", fg_item, "custom_fg_approval_required") and not _can_review(_settings()):
		frappe.throw("Only the configured FG validation/approval roles can manage pricing for this FG.", frappe.PermissionError)
