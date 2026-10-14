"""Isolated dispatch tests: no database writes or external HTTP calls."""
import unittest
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from nxtgen_savinda_pricing_calculator.api import dispatch


class TestDeliveryNoteSync(unittest.TestCase):
	def setUp(self):
		self.doc = SimpleNamespace(name="TEST-DN", docstatus=1, is_return=0, check_permission=Mock())
		self.settings = SimpleNamespace(enabled=1, base_url="https://example.invalid/", get_password=Mock(return_value="test-token"))
		self.log = SimpleNamespace(name="TEST-LOG", save=Mock())
		self.log.insert = Mock(return_value=self.log)
		self.body = [{"dispatchNumber": "TEST-DN", "jobNumber": "TEST-PP", "quantity": 105}]
		self.response = SimpleNamespace(status_code=200, text='{"success":true}')
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.get_doc = self.stack.enter_context(patch.object(dispatch.frappe, "get_doc", side_effect=[self.doc, self.log]))
		self.stack.enter_context(patch.object(dispatch.frappe, "get_single", return_value=self.settings))
		self.db = SimpleNamespace(commit=Mock(), exists=Mock(return_value=True), get_value=Mock(return_value=None))
		self.stack.enter_context(patch.object(dispatch.frappe, "db", self.db))
		self.stack.enter_context(patch.object(dispatch.frappe, "cache", SimpleNamespace(
			make_key=lambda key: key, lock=Mock(side_effect=lambda *args, **kwargs: nullcontext()))))
		self.builder = self.stack.enter_context(patch.object(dispatch, "build_dispatch_payload", return_value=self.body))
		self.post = self.stack.enter_context(patch.object(dispatch.requests, "post", return_value=self.response))
		self.http_patch = self.stack.enter_context(patch.object(dispatch.requests, "patch", return_value=self.response))

	def test_uses_existing_settings_and_array_body_with_new_endpoint(self):
		result = dispatch.sync_delivery_note("TEST-DN")
		self.assertTrue(result["ok"])
		self.post.assert_called_once_with("https://example.invalid/dispatch", json=self.body,
			headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"}, timeout=20, allow_redirects=False)
		self.settings.get_password.assert_called_once_with("auth_token")
		self.assertEqual(self.log.status, "Success")
		self.assertEqual(self.get_doc.call_args.args[0]["reference_doctype"], "Delivery Note")

	def test_disabled_settings_skip_http_and_log(self):
		self.settings.enabled = 0
		self.assertTrue(dispatch.sync_delivery_note("TEST-DN")["skipped"])
		self.post.assert_not_called()
		self.log.insert.assert_not_called()

	def test_draft_cancelled_and_return_documents_are_not_sent(self):
		self.get_doc.side_effect = None
		self.get_doc.return_value = self.doc
		for docstatus, is_return in [(0, 0), (2, 0), (1, 1)]:
			self.doc.docstatus, self.doc.is_return = docstatus, is_return
			self.assertTrue(dispatch.sync_delivery_note("TEST-DN")["skipped"])
		self.post.assert_not_called()

	def test_missing_token_is_logged_without_sending(self):
		self.settings.get_password.return_value = None
		self.assertFalse(dispatch.sync_delivery_note("TEST-DN")["ok"])
		self.assertEqual(self.log.status, "Failed")
		self.post.assert_not_called()

	def test_http_failure_is_recorded(self):
		self.response.status_code = 401
		result = dispatch.sync_delivery_note("TEST-DN")
		self.assertFalse(result["ok"])
		self.assertEqual(self.log.status, "Failed")
		self.assertEqual(self.log.http_status, 401)

	def test_network_failure_does_not_log_secret_exception_text(self):
		self.post.side_effect = requests.Timeout("test-token")
		result = dispatch.sync_delivery_note("TEST-DN")
		self.assertFalse(result["ok"])
		self.assertNotIn("test-token", self.log.error_message)

	def test_non_json_success_is_supported(self):
		self.response.text = "Accepted"
		result = dispatch.sync_delivery_note("TEST-DN")
		self.assertTrue(result["ok"])
		self.assertIsNone(result["response"])

	def test_token_echo_is_redacted_from_log_and_result(self):
		self.response.text = '{"echo":"test-token"}'
		result = dispatch.sync_delivery_note("TEST-DN")
		self.assertNotIn("test-token", self.log.response_data)
		self.assertEqual(result["response"]["echo"], "[REDACTED]")

	def test_queue_waits_for_commit(self):
		with patch.object(dispatch.frappe, "enqueue") as enqueue:
			dispatch.queue_delivery_note_sync(self.doc)
		self.assertEqual(enqueue.call_args.args[0], "nxtgen_savinda_pricing_calculator.api.dispatch.sync_delivery_note")
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

	def test_queue_skips_drafts_returns_and_cancelled_documents(self):
		with patch.object(dispatch.frappe, "enqueue") as enqueue:
			for status, is_return in [(0, 0), (2, 0), (1, 1)]:
				self.doc.docstatus, self.doc.is_return = status, is_return
				dispatch.queue_delivery_note_sync(self.doc)
			enqueue.assert_not_called()

	def test_queue_skips_missing_or_disabled_integration(self):
		with patch.object(dispatch.frappe, "enqueue") as enqueue:
			self.db.exists.return_value = False
			dispatch.queue_delivery_note_sync(self.doc)
			self.db.exists.return_value = True
			self.settings.enabled = 0
			dispatch.queue_delivery_note_sync(self.doc)
			enqueue.assert_not_called()

	def test_successful_unchanged_payload_is_not_sent_again(self):
		import json
		self.db.get_value.return_value = SimpleNamespace(name="EXISTING-SUCCESS-LOG", request_data=json.dumps(self.body, indent=2, ensure_ascii=False))
		result = dispatch.sync_delivery_note("TEST-DN")
		self.assertTrue(result["ok"] and result["skipped"])
		self.post.assert_not_called()
		self.log.insert.assert_not_called()

	def test_changed_payload_is_sent_after_previous_success(self):
		self.db.get_value.return_value = SimpleNamespace(name="EXISTING-SUCCESS-LOG", request_data="[]")
		self.assertTrue(dispatch.sync_delivery_note("TEST-DN")["ok"])
		self.post.assert_called_once()

	def test_save_hooks_reference_new_sync_without_replacing_packing_hooks(self):
		from nxtgen_savinda_pricing_calculator import hooks
		events = hooks.doc_events["Delivery Note"]
		for event in ("on_update", "on_update_after_submit"):
			self.assertEqual(events[event], "nxtgen_savinda_pricing_calculator.api.dispatch.queue_delivery_note_sync")
		self.assertTrue(events["on_submit"].endswith("mark_packings_delivered"))
		self.assertTrue(events["on_cancel"][0].endswith("mark_packings_delivered"))
		self.assertEqual(events["on_cancel"][1], "nxtgen_savinda_pricing_calculator.api.dispatch.queue_delivery_note_cancellation")

	def test_cancellation_patch_uses_same_settings_and_logs_object_payload(self):
		self.doc.docstatus = 2
		result = dispatch.sync_delivery_note_cancellation("TEST-DN")
		self.assertTrue(result["ok"])
		self.http_patch.assert_called_once_with(
			"https://example.invalid/dispatch/status/dispatch-number/TEST-DN", json={"status": "cancelled"},
			headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"}, timeout=20, allow_redirects=False)
		self.post.assert_not_called()
		self.builder.assert_not_called()
		self.assertEqual(self.log.status, "Success")
		self.assertIn('"status": "cancelled"', self.get_doc.call_args.args[0]["request_data"])

	def test_cancellation_queue_waits_for_commit(self):
		self.doc.docstatus = 2
		with patch.object(dispatch.frappe, "enqueue") as enqueue:
			dispatch.queue_delivery_note_cancellation(self.doc, "on_cancel")
		self.assertEqual(enqueue.call_args.args[0], "nxtgen_savinda_pricing_calculator.api.dispatch.sync_delivery_note_cancellation")
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

	def test_cancellation_skips_submitted_drafts_and_returns(self):
		self.get_doc.side_effect = None
		self.get_doc.return_value = self.doc
		with patch.object(dispatch.frappe, "enqueue") as enqueue:
			for status, is_return in [(0, 0), (1, 0), (2, 1)]:
				self.doc.docstatus, self.doc.is_return = status, is_return
				dispatch.queue_delivery_note_cancellation(self.doc)
				self.assertTrue(dispatch.sync_delivery_note_cancellation("TEST-DN")["skipped"])
			enqueue.assert_not_called()
		self.http_patch.assert_not_called()

	def test_cancellation_failure_is_logged(self):
		self.doc.docstatus = 2
		self.response.status_code = 404
		self.assertFalse(dispatch.sync_delivery_note_cancellation("TEST-DN")["ok"])
		self.assertEqual(self.log.status, "Failed")
		self.assertEqual(self.log.http_status, 404)

	def test_successful_cancellation_is_not_sent_twice(self):
		import json
		self.doc.docstatus = 2
		self.db.get_value.return_value = SimpleNamespace(name="CANCEL-SUCCESS", request_data=json.dumps({"status":"cancelled"}, indent=2))
		self.assertTrue(dispatch.sync_delivery_note_cancellation("TEST-DN")["skipped"])
		self.http_patch.assert_not_called()

	def test_cancellation_encodes_dispatch_number_in_url(self):
		self.doc.docstatus = 2
		self.doc.name = "AOD/2026 01"
		dispatch.sync_delivery_note_cancellation(self.doc.name)
		self.assertTrue(self.http_patch.call_args.args[0].endswith("AOD%2F2026%2001"))
