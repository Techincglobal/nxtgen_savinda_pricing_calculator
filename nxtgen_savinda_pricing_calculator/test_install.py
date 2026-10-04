import unittest
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator import install


class TestCustomFieldUpgrade(unittest.TestCase):
	def test_all_local_pp_fields_are_declared(self):
		fields = {field["fieldname"]: field for field in install._custom_field_definitions()["Production Plan"]}
		for name in ("custom_repeat_or_new", "custom_is_callout", "custom_tab_3", "custom_manufacturing_planning_flexo"):
			self.assertIn(name, fields)
		for name in ("custom_repeat_or_new", "custom_is_callout"):
			self.assertEqual(fields[name]["hidden"], 0)
			self.assertEqual(fields[name]["allow_on_submit"], 1)

	def test_install_and_migration_both_reconcile_custom_fields(self):
		import ast
		import inspect
		for hook in (install.after_install, install.after_migrate):
			tree = ast.parse(inspect.getsource(hook))
			calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
			self.assertIn("_ensure_custom_fields", calls)

	def test_missing_field_fails_upgrade(self):
		meta = Mock()
		meta.has_field.side_effect = lambda name: name != "custom_is_callout"
		with patch.object(frappe, "clear_cache"), patch.object(frappe, "get_meta", return_value=meta), patch.object(
			install.frappe, "db", new=Mock(), create=True
		) as db, patch.object(frappe, "throw", side_effect=frappe.ValidationError) as throw:
			db.has_column.return_value = True
			with self.assertRaises(frappe.ValidationError):
				install._verify_production_plan_fields()
			self.assertIn("Production Plan.custom_is_callout", throw.call_args.args[0])

	def test_failed_custom_field_creation_is_not_swallowed(self):
		with patch("frappe.custom.doctype.custom_field.custom_field.create_custom_fields", side_effect=RuntimeError("schema failure")):
			with self.assertRaisesRegex(RuntimeError, "schema failure"):
				install._ensure_custom_fields()
