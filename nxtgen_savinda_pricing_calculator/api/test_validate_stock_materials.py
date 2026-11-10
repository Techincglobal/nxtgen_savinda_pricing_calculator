import unittest
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api.production_plan import validate_stock_materials


class TestValidateStockMaterials(unittest.TestCase):
    def plan(self, rows, previous="Supply Chain Validation", current="Approved"):
        return frappe._dict(
            workflow_state=current, mr_items=rows,
            get_doc_before_save=Mock(return_value=frappe._dict(workflow_state=previous) if previous else None),
        )

    def test_empty_table_or_blank_item_does_not_count(self):
        for rows in ([], [frappe._dict(material_request_type="Material Transfer")]):
            with self.subTest(rows=rows), patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
                validate_stock_materials(self.plan(rows))

    def test_all_rows_must_be_transfers(self):
        for request_type in ("Purchase", "", None, "Material Issue", "Manufacture", "Customer Provided"):
            rows = [frappe._dict(item_code="FILM", material_request_type="Material Transfer"),
                    frappe._dict(item_code="INK", material_request_type=request_type)]
            with self.subTest(request_type=request_type), patch.object(frappe, "throw", side_effect=ValueError) as throw, self.assertRaises(ValueError):
                validate_stock_materials(self.plan(rows))
            self.assertIn("row 2", throw.call_args.args[0])

    def test_multiple_transfer_materials_pass(self):
        rows = [frappe._dict(item_code=item, material_request_type="Material Transfer") for item in ("FILM", "INK")]
        with patch.object(frappe, "throw") as throw:
            validate_stock_materials(self.plan(rows))
            throw.assert_not_called()

    def test_unrelated_transitions_and_saves_are_unaffected(self):
        for previous, current in ((None, "Draft"), ("BOM Validation", "Supply Chain Validation"),
                                  ("Supply Chain Validation", "Supply Chain Validation"),
                                  ("Approved", "Approved"), ("Approved", "Submitted")):
            with self.subTest(previous=previous, current=current), patch.object(frappe, "throw") as throw:
                validate_stock_materials(self.plan([], previous, current))
                throw.assert_not_called()
