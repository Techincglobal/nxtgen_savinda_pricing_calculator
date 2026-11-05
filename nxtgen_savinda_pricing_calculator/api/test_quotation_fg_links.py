import unittest
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api import manufacturing


class TestQuotationFGLinks(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.utils.get_datetime = frappe.utils.get_datetime
        self.api.utils.now.return_value = "2026-11-05 12:00:01"
        self.api.parse_json = lambda value: value
        self.api.throw.side_effect = ValueError
        self.doc = Mock(name='quotation')
        self.doc.name = 'SQ-test'
        self.doc.docstatus = 1
        self.doc.modified = '2026-11-05 12:00:00'
        self.doc.items = [frappe._dict(name='row-1', finish_good='')]
        self.item = Mock(has_variants=0)
        self.api.get_doc.side_effect = lambda dt, *a, **k: self.doc if dt == 'Savinda Quotation' else self.item
        self.patch = patch.object(manufacturing, 'frappe', self.api)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def link(self, row='row-1', modified='2026-11-05 12:00:00'):
        return manufacturing.link_quotation_fg_items.__wrapped__('SQ-test', [dict(row=row, item_code='FG-1')], modified)

    def test_submitted_link_checks_write_not_submit_and_only_changes_fg(self):
        self.assertEqual(self.link(), {'updated': 1})
        self.doc.check_permission.assert_called_once_with('write')
        self.item.check_permission.assert_called_once_with('read')
        self.api.db.set_value.assert_any_call('Savinda Quotation Item', 'row-1', 'finish_good', 'FG-1', update_modified=False)
        self.doc.save.assert_not_called()
        self.doc.submit.assert_not_called()

    def test_no_write_permission_blocks_updates(self):
        self.doc.check_permission.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.link()
        self.api.db.set_value.assert_not_called()

    def test_cancelled_stale_or_foreign_rows_are_rejected(self):
        self.doc.docstatus = 2
        with self.assertRaises(ValueError):
            self.link()
        self.doc.docstatus = 1
        with self.assertRaises(ValueError):
            self.link(modified='2026-11-04 12:00:00')
        with self.assertRaises(ValueError):
            self.link(row='another-quotation-row')
        self.api.db.set_value.assert_not_called()

    def test_existing_links_cannot_be_overwritten(self):
        self.doc.items[0].finish_good = 'OTHER-FG'
        with self.assertRaises(ValueError):
            self.link()
        self.api.db.set_value.assert_not_called()

    def test_same_link_is_idempotent(self):
        self.doc.items[0].finish_good = 'FG-1'
        self.assertEqual(self.link(), {'updated': 0})
        self.api.db.set_value.assert_not_called()
