import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator import install
from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils.jinja import get_quotation_price_basis, get_quotation_print_rate
from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.savinda_quotation import savinda_quotation as quotation


class TestQuotationPriceDisplay(unittest.TestCase):
	def test_creation_fetches_customer_currency_rate_before_pricing(self):
		doc = SimpleNamespace(currency=None, customer="CUSTOMER", conversion_rate=1,
			date="2026-11-03", cost_sheet=None, _fetch_from_cost_sheet=Mock(), _fetch_from_inquiry=Mock())
		doc._fetch_creation_exchange_rate = lambda: quotation.SavindaQuotation._fetch_creation_exchange_rate(doc)
		with patch.object(quotation, "_customer_currency", return_value="USD"), patch(
			"erpnext.setup.utils.get_exchange_rate", return_value=337.14
		) as fetch:
			quotation.SavindaQuotation.before_insert(doc)
			fetch.assert_called_once_with("USD", "LKR", "2026-11-03", args="for_selling")
			self.assertEqual(doc.conversion_rate, 337.14)

	def test_creation_base_currency_needs_no_rate_lookup(self):
		doc = SimpleNamespace(currency="LKR", conversion_rate=99)
		with patch("erpnext.setup.utils.get_exchange_rate") as fetch:
			quotation.SavindaQuotation._fetch_creation_exchange_rate(doc)
			fetch.assert_not_called()
			self.assertEqual(doc.conversion_rate, 1)

	def test_system_lkr_default_does_not_hide_usd_customer(self):
		doc = SimpleNamespace(currency="LKR", customer="CUSTOMER", conversion_rate=1,
			date="2026-11-04", cost_sheet="CS", _fetch_from_cost_sheet=Mock(), _fetch_from_inquiry=Mock(),
			get=lambda key: None)
		doc._fetch_creation_exchange_rate = lambda: quotation.SavindaQuotation._fetch_creation_exchange_rate(doc)
		with patch.object(quotation, "_customer_currency", return_value="USD"), patch.object(frappe, "db", Mock()) as db, patch(
			"erpnext.setup.utils.get_exchange_rate", return_value=337.14):
			db.get_value.return_value = False
			quotation.SavindaQuotation.before_insert(doc)
			self.assertEqual((doc.currency, doc.conversion_rate), ("USD", 337.14))

	def test_creation_rejects_missing_or_invalid_exchange_rate(self):
		for rate in (None, 0, -1, float("nan"), float("inf")):
			with self.subTest(rate=rate), patch("erpnext.setup.utils.get_exchange_rate", return_value=rate), patch.object(
				frappe, "throw", side_effect=frappe.ValidationError
			), self.assertRaises(frappe.ValidationError):
				quotation.SavindaQuotation._fetch_creation_exchange_rate(
					SimpleNamespace(currency="USD", date="2026-11-03", conversion_rate=1))

	def test_save_preserves_reviewed_exchange_rate(self):
		doc = SimpleNamespace(currency="USD", conversion_rate=335)
		quotation.SavindaQuotation._validate_currency(doc)
		self.assertEqual(doc.conversion_rate, 335)

	def setUp(self):
		base = patch.object(quotation, "_company_currency", return_value="LKR")
		base.start()
		self.addCleanup(base.stop)
		rounding = patch.object(frappe, "get_system_settings", return_value="Banker's Rounding")
		rounding.start()
		self.addCleanup(rounding.stop)

	def test_foreign_currencies_show_per_thousand_with_four_decimals(self):
		for currency in ("USD", "EUR", "GBP"):
			with self.subTest(currency=currency):
				doc = frappe._dict(currency=currency, conversion_rate=300, quote_by_pieces=1, price_basis_qty=1000)
				row = frappe._dict(base_selling_price=24.554, selling_price=24.554 / 300)
				self.assertEqual(get_quotation_print_rate(doc, row), "81.8467")
				self.assertEqual(get_quotation_price_basis(doc)["label"], f"Price per 1,000 Pieces ({currency})")

	def test_local_currency_stays_per_piece_two_decimal_print(self):
		self.assertEqual(get_quotation_print_rate(frappe._dict(currency="LKR", conversion_rate=1),
			frappe._dict(base_selling_price=24.554)), "24.55")
		self.assertEqual(get_quotation_price_basis("LKR")["multiplier"], 1)

	def test_scale_before_rounding_tiny_rate(self):
		self.assertEqual(get_quotation_print_rate(frappe._dict(currency="USD", conversion_rate=300, quote_by_pieces=1, price_basis_qty=1000),
			frappe._dict(base_selling_price=0.01)), "0.0333")

	def test_legacy_transaction_price_is_not_converted_twice(self):
		self.assertEqual(get_quotation_print_rate(frappe._dict(currency="USD", conversion_rate=300, quote_by_pieces=1, price_basis_qty=1000),
			frappe._dict(selling_price=0.123456)), "123.4560")

	def test_quote_sync_preserves_per_piece_rate_and_base_anchor(self):
		row = frappe._dict(base_selling_price=24.554, selling_price=0)
		doc = SimpleNamespace(currency="USD", conversion_rate=300, items=[row])
		quotation.SavindaQuotation._sync_item_currency(doc)
		self.assertEqual(row.base_selling_price, 24.554)
		self.assertAlmostEqual(row.selling_price, 24.554 / 300)
		self.assertEqual(row.price_per_1000, 81.8467)

	def test_legacy_format_upgrade_changes_only_price_heading_and_rate(self):
		heading = "Price Per Piece ({{ doc.currency or 'LKR' }})"
		rate = "{{ '{:.2f}'.format(((row.base_selling_price or 0) / (doc.conversion_rate or 1)) or (row.selling_price or 0)) }}"
		original = "<div>Custom company header</div>" + heading + rate + "<div>Custom terms</div>"
		with patch.object(frappe, "get_all", return_value=[frappe._dict(name="Quotation-Savinda", html=original)]), patch.object(frappe, "db", Mock()) as db:
			install._upgrade_quotation_price_layouts()
			db.set_value.assert_called_once_with("Print Format", "Quotation-Savinda", "html", original.replace(heading, "{{ get_quotation_price_basis(doc).label | e }}").replace(rate, "{{ get_quotation_print_rate(doc, row) }}"))

	def test_unrecognised_formats_are_not_rewritten(self):
		with patch.object(frappe, "get_all", return_value=[frappe._dict(name="Custom", html="Custom pricing layout")]), patch.object(frappe, "get_doc") as get_doc:
			install._upgrade_quotation_price_layouts()
		get_doc.assert_not_called()

	def test_checkbox_controls_all_currencies_and_custom_quantity(self):
		for currency in ("LKR", "USD"):
			doc = frappe._dict(currency=currency, conversion_rate=1, quote_by_pieces=0, price_basis_qty=500)
			self.assertEqual(get_quotation_price_basis(doc)["multiplier"], 1)
			doc.quote_by_pieces = 1
			self.assertEqual(get_quotation_price_basis(doc)["multiplier"], 500)
			self.assertEqual(get_quotation_price_basis(doc)["label"], f"Price per 500 Pieces ({currency})")
			row = frappe._dict(selling_price=2)
			self.assertEqual(get_quotation_print_rate(doc, row), "1,000.00" if currency == "LKR" else "1,000.0000")
			self.assertEqual(row.selling_price, 2)

	def test_invalid_group_quantity_rejected_on_update_after_submit(self):
		for qty in (0, -1, 2.5):
			doc = frappe._dict(quote_by_pieces=1, price_basis_qty=qty)
			with patch.object(frappe, "throw", side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
				quotation.SavindaQuotation._validate_print_price_basis(doc)

	def test_current_print_heading_migrates_without_layout_changes(self):
		original = "Custom header {{ get_quotation_price_basis(doc.currency).label }} Custom footer"
		with patch.object(frappe, "get_all", return_value=[frappe._dict(name="Custom", html=original)]), patch.object(frappe, "db", Mock()) as db:
			install._upgrade_quotation_price_layouts()
			db.set_value.assert_called_once_with("Print Format", "Custom", "html", original.replace("doc.currency", "doc"))
