import unittest
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api.production_plan import validate_confirm_bom


class TestConfirmBOMValidation(unittest.TestCase):
    def plan(self, pricing_type="Offset", previous="BOM Validation", current="Supply Chain Validation"):
        return frappe._dict(
            workflow_state=current,
            get_doc_before_save=Mock(return_value=frappe._dict(workflow_state=previous) if previous else None),
            custom_pricing_type=pricing_type,
            custom_offset_planning=[frappe._dict(fg_item="OFFSET-FG")],
            custom_flexo_planning=[frappe._dict(fg_item="FLEXO-FG")],
            po_items=[frappe._dict(item_code="FG")],
            sub_assembly_items=[frappe._dict(production_item="SFG")],
        )

    def test_valid_offset_and_flexo_ignore_other_planning_table(self):
        for pricing_type, unused in (("Offset", "custom_flexo_planning"), ("Flexo", "custom_offset_planning")):
            doc = self.plan(pricing_type)
            doc[unused] = []
            with patch.object(frappe, "throw") as throw:
                validate_confirm_bom(doc)
                throw.assert_not_called()

    def test_each_required_table_rejects_empty_and_blank_rows(self):
        for pricing_type, planning in (("Offset", "custom_offset_planning"), ("Flexo", "custom_flexo_planning")):
            for table in (planning, "po_items", "sub_assembly_items"):
                for rows in ([], [frappe._dict()]):
                    with self.subTest(pricing_type=pricing_type, table=table, rows=rows):
                        doc = self.plan(pricing_type)
                        doc[table] = rows
                        with patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
                            validate_confirm_bom(doc)

    def test_other_transitions_and_ordinary_saves_are_unaffected(self):
        for previous, current in ((None, "Draft"), ("Draft", "BOM Validation"),
                                  ("BOM Validation", "BOM Validation"),
                                  ("Supply Chain Validation", "Supply Chain Validation"),
                                  ("BOM Validation", "Rejected")):
            doc = self.plan(previous=previous, current=current)
            doc.custom_offset_planning = doc.po_items = doc.sub_assembly_items = []
            with patch.object(frappe, "throw") as throw:
                validate_confirm_bom(doc)
                throw.assert_not_called()

    def test_missing_type_and_tables_are_reported_together(self):
        doc = self.plan(pricing_type="")
        doc.po_items = doc.sub_assembly_items = []
        with patch.object(frappe, "throw", side_effect=ValueError) as throw, self.assertRaises(ValueError):
            validate_confirm_bom(doc)
        message = throw.call_args.args[0]
        for label in ("pricing type", "Assembly Items", "Sub Assembly Items"):
            self.assertIn(label, message)
