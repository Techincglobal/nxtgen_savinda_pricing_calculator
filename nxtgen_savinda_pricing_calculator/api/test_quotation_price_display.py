import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator import install
from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils.jinja import get_quotation_price_basis, get_quotation_print_rate
from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.doctype.savinda_quotation import savinda_quotation as quotation


class TestQuotationPriceDisplay(unittest.TestCase):
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
				doc = frappe._dict(currency=currency, conversion_rate=300)
				row = frappe._dict(base_selling_price=24.554, selling_price=24.554 / 300)
				self.assertEqual(get_quotation_print_rate(doc, row), "81.8467")
				self.assertEqual(get_quotation_price_basis(currency)["label"], f"Price per 1,000 Units ({currency})")

	def test_local_currency_stays_per_piece_two_decimal_print(self):
		self.assertEqual(get_quotation_print_rate(frappe._dict(currency="LKR", conversion_rate=1),
			frappe._dict(base_selling_price=24.554)), "24.55")
		self.assertEqual(get_quotation_price_basis("LKR")["multiplier"], 1)

	def test_scale_before_rounding_tiny_rate(self):
		self.assertEqual(get_quotation_print_rate(frappe._dict(currency="USD", conversion_rate=300),
			frappe._dict(base_selling_price=0.01)), "0.0333")

	def test_legacy_transaction_price_is_not_converted_twice(self):
		self.assertEqual(get_quotation_print_rate(frappe._dict(currency="USD", conversion_rate=300),
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
		pf = Mock()
		with patch.object(frappe, "get_all", return_value=[frappe._dict(name="Quotation-Savinda", html=original)]), patch.object(frappe, "get_doc", return_value=pf):
			install._upgrade_quotation_price_layouts()
		self.assertEqual(pf.html, original.replace(heading, "{{ get_quotation_price_basis(doc.currency).label | e }}").replace(rate, "{{ get_quotation_print_rate(doc, row) }}"))
		pf.save.assert_called_once_with(ignore_permissions=True)

	def test_unrecognised_formats_are_not_rewritten(self):
		with patch.object(frappe, "get_all", return_value=[frappe._dict(name="Custom", html="Custom pricing layout")]), patch.object(frappe, "get_doc") as get_doc:
			install._upgrade_quotation_price_layouts()
		get_doc.assert_not_called()
