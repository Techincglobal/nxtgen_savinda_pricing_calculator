import unittest
from unittest.mock import Mock, patch

from jinja2 import Environment
import frappe
from nxtgen_savinda_pricing_calculator import install


class TestOffsetTicketVariantExpiry(unittest.TestCase):
	def test_variant_only_size_and_free_text_expiry_preserve_header(self):
		old = '<header>{{ h.ctn_size }}</header><td>{{ item.variant_value or item.size }}</td><td>{% if item.exp_date %}{{ frappe.utils.formatdate(item.exp_date) }}{% endif %}</td>'
		updated = install._offset_ticket_variant_expiry_html(old)
		for variant, expected in (("", ""), (None, ""), ("500 ml", "500 ml")):
			with self.subTest(variant=variant):
				html = Environment().from_string(updated).render(h={"ctn_size":"HEADER SIZE"},
					item={"variant_value":variant,"size":"ACTUAL SIZE","exp_date":"EXPECTED DATE","expiry_date":"DEC 2028"})
				self.assertEqual(html, '<header>HEADER SIZE</header><td>' + expected + '</td><td>DEC 2028</td>')
		self.assertEqual(install._offset_ticket_variant_expiry_html(updated), updated)

	def test_missing_expiry_is_blank_not_expected_date(self):
		updated = install._offset_ticket_variant_expiry_html('{% if item.exp_date %}{{ frappe.utils.formatdate(item.exp_date) }}{% endif %}')
		self.assertEqual(Environment().from_string(updated).render(item={"exp_date":"2026-11-01"}), "")

	def test_only_requested_format_is_saved(self):
		pf = Mock(doc_type="Production Plan", html="{{ item.variant_value or item.size }}")
		with patch.object(frappe, "db", Mock()) as db, patch.object(frappe, "get_doc", return_value=pf) as load:
			db.exists.return_value = True
			install._upgrade_offset_ticket_variant_expiry()
			load.assert_called_once_with("Print Format", "Offset Job Ticket - 1")
		pf.save.assert_called_once_with(ignore_permissions=True)

	def test_already_upgraded_format_is_not_saved_again(self):
		pf = Mock(doc_type="Production Plan", html='{{ item.variant_value or "" }} {{ (item.expiry_date or "") | e }}')
		with patch.object(frappe, "db", Mock()) as db, patch.object(frappe, "get_doc", return_value=pf):
			db.exists.return_value = True
			install._upgrade_offset_ticket_variant_expiry()
		pf.save.assert_not_called()
