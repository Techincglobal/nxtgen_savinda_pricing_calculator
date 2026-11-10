import json
import unittest
from unittest.mock import Mock, patch

import frappe
from nxtgen_savinda_pricing_calculator.api import offset_calculator as calculator


class TestCopyCalculationSplits(unittest.TestCase):
    def test_copy_preserves_splits_but_changed_quantity_resets_them(self):
        if not hasattr(frappe.local, "flags"):
            frappe.local.flags = frappe._dict(in_test=False)
            self.addCleanup(lambda: delattr(frappe.local, "flags"))
        cases = [
            (None, "Breakdown", [6000, 4000], "Breakdown", 10000),
            (10000, "Breakdown", [6000, 4000], "Breakdown", 10000),
            (None, None, [6000, 4000], "Breakdown", 10000),
            (12000, "Breakdown", [], "Required", 12000),
            (None, "Required", [6000, 4000], "Required", 10000),
        ]
        if not hasattr(frappe.local, "db"):
            frappe.local.db = Mock()
            self.addCleanup(lambda: delattr(frappe.local, "db"))
        for new_qty, mode, splits, expected_mode, total in cases:
            with self.subTest(new_qty=new_qty, mode=mode):
                form = {"item_qty": 10000, "breakdown_qtys": [6000, 4000]}
                if mode:
                    form["board_count_mode"] = mode
                source = frappe._dict(ui_state=json.dumps({"form": form}), finishing=[])
                target = Mock()
                with (
                    patch.object(frappe, "get_doc", side_effect=[source, target]),
                    patch.object(calculator, "calculate", return_value={"pricing": {"unit_cost": 5}}) as calculate,
                    patch.object(calculator, "save_costing", return_value={"doc_name": "NEW-CB"}) as save,
                    patch.object(calculator, "sync_cost_item_unit_cost"),
                    patch.object(frappe.db, "commit"),
                ):
                    result = calculator.copy_calculation("SOURCE-CB", "TARGET-CI", new_qty=new_qty)
                self.assertEqual(result["new_cb"], "NEW-CB")
                for call in (calculate, save):
                    copied = json.loads(call.call_args.args[0])["form"]
                    self.assertEqual(copied["breakdown_qtys"], splits)
                    self.assertEqual(copied["board_count_mode"], expected_mode)
                    self.assertEqual(copied["item_qty"], total)
                self.assertEqual(json.loads(source.ui_state)["form"], form)
