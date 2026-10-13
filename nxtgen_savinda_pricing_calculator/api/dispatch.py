"""Build and send Delivery Note dispatch payloads.

Save/update hooks use queue_delivery_note_sync with the existing production
tracker settings. The earlier environment-token preview functions are retained
but are not registered in hooks. No automatic retries or cancellation sync.
"""

import os

import frappe
import requests
from frappe.utils import flt


def build_dispatch_payload(doc):
	"""Prepare one dispatch record per Production Plan without sending anything."""
	quantities = {}
	stock_uoms = {}

	for row in doc.items:
		if not row.get("custom_packing"):
			frappe.throw(f"Row {row.idx}: Packing reference is required for dispatch.")

		pp = frappe.db.get_value("Packing", row.custom_packing, "job__npd_number")
		if not pp:
			frappe.throw(f"Row {row.idx}: Packing has no Production Plan.")

		# Both paid and zero-rate Extra rows contribute to physical dispatch qty.
		qty = flt(row.stock_qty)
		if qty <= 0:
			frappe.throw(f"Row {row.idx}: Delivery quantity must be positive.")
		if not row.stock_uom:
			frappe.throw(f"Row {row.idx}: Stock UOM is required for dispatch.")

		quantities[pp] = quantities.get(pp, 0) + qty
		stock_uoms.setdefault(pp, set()).add(row.stock_uom)

	payload = []
	for pp, qty in quantities.items():
		if len(stock_uoms[pp]) != 1:
			frappe.throw(f"{pp}: Mixed stock UOMs cannot be sent as one quantity.")

		payload.append({
			"dispatchNumber": doc.name,
			"jobNumber": pp,
			"customer": doc.customer_name or doc.customer,
			"dispatchDate": str(doc.posting_date),
			"quantity": flt(qty, 6),
			"status": "delivered",
			"remarks": doc.get("remarks") or "",
		})

	return payload


def queue_dispatch(doc, method=None):
	"""Proposed Delivery Note on_submit hook; enqueue only after DB commit."""
	if doc.is_return:
		return

	payload = build_dispatch_payload(doc)
	if payload:
		frappe.enqueue(
			"nxtgen_savinda_pricing_calculator.api.dispatch.send_dispatch",
			queue="short",
			enqueue_after_commit=True,
			delivery_note=doc.name,
			payload=payload,
		)


def send_dispatch(delivery_note, payload):
	"""Send a submitted DN's queued payload; never expose credentials in logs."""
	if frappe.db.get_value("Delivery Note", delivery_note, "docstatus") != 1:
		return

	try:
		token = os.environ.get("DISPATCH_API_JWT")
		if not token:
			raise RuntimeError("Dispatch JWT is not configured")

		response = requests.post(
			"https://api.dev.pt.savinda.lk/dispatch",
			headers={
				"Content-Type": "application/json",
				"Authorization": f"Bearer {token}",
			},
			json=payload,
			timeout=(5, 30),
			allow_redirects=False,
		)
		if not 200 <= response.status_code < 300:
			raise RuntimeError(f"Dispatch API returned HTTP {response.status_code}")

	except Exception as exc:
		# No JWT, headers, payload, response body or raw exception text in logs.
		frappe.log_error(
			title="Delivery dispatch API failed",
			message=f"Delivery Note: {delivery_note}\nFailure type: {type(exc).__name__}",
			reference_doctype="Delivery Note",
			reference_name=delivery_note,
		)
		raise


def queue_delivery_note_sync(doc, method=None):
	"""Save/update handler using the existing tracker settings.

	Do not call sync_delivery_note directly from a
	transaction hook: the outbound audit log intentionally commits its updates.
	"""
	if doc.docstatus != 1 or doc.is_return:
		return
	# This integration app is optional; do not break ordinary DN saves when absent.
	if not frappe.db.exists("DocType", "Savinda Production Tracker Settings"):
		return
	if not frappe.get_single("Savinda Production Tracker Settings").enabled:
		return
	frappe.enqueue(
		"nxtgen_savinda_pricing_calculator.api.dispatch.sync_delivery_note",
		queue="short",
		enqueue_after_commit=True,
		delivery_note=doc.name,
	)


def sync_delivery_note(delivery_note):
	"""Serialize workers per DN so simultaneous save events do not double-send."""
	key = frappe.cache.make_key(f"delivery-note-dispatch:{delivery_note}")
	with frappe.cache.lock(key, timeout=120, blocking_timeout=25):
		return _sync_delivery_note(delivery_note)


def _sync_delivery_note(delivery_note):
	"""New standalone DN sync, independent of _push_job_card.

	Use in a background worker after submission commits, or explicitly against
	an already submitted DN. No automatic retry or cancellation sync is enabled.
	"""
	import json
	from urllib.parse import urlsplit

	from frappe.utils.response import json_handler

	doc = frappe.get_doc("Delivery Note", delivery_note)
	doc.check_permission("read")
	if doc.docstatus != 1 or doc.is_return:
		return {"ok": False, "skipped": True, "reason": "Only submitted, non-return Delivery Notes can be synced."}

	settings = frappe.get_single("Savinda Production Tracker Settings")
	if not settings.enabled:
		return {"ok": False, "skipped": True, "reason": "Savinda Production Tracker Settings is disabled."}
	base_url = (settings.base_url or "").strip().rstrip("/")
	url = urlsplit(base_url)
	if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment:
		frappe.throw("Set a valid HTTPS Base URL in Savinda Production Tracker Settings.")

	body = build_dispatch_payload(doc)
	if not body:
		return {"ok": False, "skipped": True, "reason": "No dispatch items found."}
	endpoint = f"{base_url}/dispatch"
	request_data = json.dumps(body, indent=2, ensure_ascii=False, default=json_handler)
	previous_log = frappe.db.get_value("Savinda Production Tracker Log", {
		"direction": "Outbound", "reference_doctype": "Delivery Note", "reference_name": doc.name,
		"endpoint": endpoint, "status": "Success",
	}, ["name", "request_data"], as_dict=True, order_by="creation desc")
	if previous_log and previous_log.request_data == request_data:
		return {"ok": True, "skipped": True, "reason": "This dispatch payload was already synced successfully.", "log": previous_log.name}
	log = frappe.get_doc({
		"doctype": "Savinda Production Tracker Log",
		"direction": "Outbound",
		"reference_doctype": "Delivery Note",
		"reference_name": doc.name,
		"endpoint": endpoint,
		"request_data": request_data,
		"status": "Pending",
	}).insert(ignore_permissions=True)
	frappe.db.commit()

	try:
		token = settings.get_password("auth_token")
		if not token:
			raise RuntimeError("Auth Token is not set in Savinda Production Tracker Settings")
		response = requests.post(
			endpoint,
			json=body,
			headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
			timeout=20,
			allow_redirects=False,
		)
	except Exception as exc:
		# Do not put raw exceptions/request headers (which may contain secrets) in logs.
		log.status = "Failed"
		log.error_message = f"Dispatch request failed ({type(exc).__name__}). Check token/configuration and connectivity."
		log.save(ignore_permissions=True)
		frappe.db.commit()
		return {"ok": False, "error": log.error_message, "log": log.name}

	log.http_status = response.status_code
	log.response_data = response.text.replace(token, "[REDACTED]")
	if not 200 <= response.status_code < 300:
		log.status = "Failed"
		log.error_message = f"Dispatch API returned HTTP {response.status_code}. See response in the integration log."
		log.save(ignore_permissions=True)
		frappe.db.commit()
		return {"ok": False, "error": log.error_message, "log": log.name}

	log.status = "Success"
	log.save(ignore_permissions=True)
	frappe.db.commit()
	try:
		response_json = json.loads(log.response_data)
	except ValueError:
		response_json = None
	return {"ok": True, "log": log.name, "response": response_json}
