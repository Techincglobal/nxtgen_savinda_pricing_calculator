import unittest
from unittest.mock import Mock, patch

import frappe

from nxtgen_savinda_pricing_calculator import install


class TestPreserveInstalledData(unittest.TestCase):
	def test_existing_masters_and_child_tables_are_skipped(self):
		for dt in ("Cost Fact", "Offset Machine", "Offset Ink", "Flexo Foil", "Offset Spec", "Item", "Print Format", "Workspace"):
			with self.subTest(doctype=dt), patch.object(frappe, "get_meta", return_value=Mock(issingle=False)), patch.object(
				frappe, "db", Mock()
			) as db, patch.object(frappe, "get_doc") as get_doc:
				db.table_exists.return_value = True
				db.exists.return_value = True
				status, _ = install._create_if_missing({"doctype": dt, "name": "EXISTING", "items": [{"rate": 999}]})
				self.assertEqual(status, "already_exists")
				get_doc.assert_not_called()
				db.set_value.assert_not_called()

	def test_existing_single_configuration_is_never_saved(self):
		with patch.object(frappe, "get_meta", return_value=Mock(issingle=True)), patch.object(
			frappe, "db", Mock()
		) as db, patch.object(frappe, "get_doc") as get_doc:
			db.exists.return_value = True
			self.assertEqual(install._create_if_missing({"doctype":"Costing Configuration", "vat_rate":99})[0], "already_exists")
			get_doc.assert_not_called()

	def test_missing_master_is_still_inserted(self):
		with patch.object(frappe, "get_meta", return_value=Mock(issingle=False)), patch.object(
			frappe, "db", Mock()
		) as db, patch.object(frappe, "get_doc") as get_doc:
			db.exists.return_value = False
			db.table_exists.return_value = True
			get_doc.return_value.name = "NEW"
			self.assertEqual(install._create_if_missing({"doctype":"Cost Fact", "name":"NEW"})[0], "created")
			get_doc.return_value.insert.assert_called_once()
			get_doc.return_value.save.assert_not_called()

	def test_all_existing_workflows_are_preserved(self):
		for method in (install._ensure_workflow, install._ensure_quotation_workflow,
			install._ensure_sales_order_npd_workflow, install._ensure_npd_request_workflow, install._ensure_packing_workflow):
			with self.subTest(method=method.__name__), patch.object(frappe, "db", Mock()) as db, patch.object(frappe, "get_doc") as get_doc:
				db.table_exists.return_value = True
				# The app-named workflow is absent, but a site-customized workflow exists.
				db.exists.side_effect = [False, True]
				method()
				get_doc.assert_not_called()
				db.set_value.assert_not_called()

	def test_managed_print_formats_update_without_importing_master_data(self):
		with patch.object(frappe, "db", Mock()) as db, patch.object(frappe, "get_doc") as get_doc, patch.object(frappe, "new_doc") as new_doc, patch(
			"nxtgen_savinda_pricing_calculator.api.costing_bundle._ensure_combined_print_format"
		) as combined:
			db.exists.return_value = True
			install._sync_code_print_formats()
			self.assertTrue(get_doc.called)
			self.assertTrue(all(call.args[0] == "Print Format" for call in get_doc.call_args_list))
			get_doc.return_value.save.assert_called_with(ignore_permissions=True)
			new_doc.assert_not_called()
			combined.assert_called_once_with(overwrite=True)

	def test_install_update_do_not_run_legacy_transaction_rewrites(self):
		import ast
		import inspect
		for hook in (install.after_install, install.after_migrate):
			calls = {n.func.id for n in ast.walk(ast.parse(inspect.getsource(hook))) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
			self.assertTrue(calls.isdisjoint({"_migrate_flexo_planning_rows", "_migrate_artwork_approval_plans_to_supply_chain", "_retire_sales_order_npd_workflow"}))
			self.assertIn("_sync_code_print_formats", calls)

	def test_migration_hooks_enable_schema_and_layout_updates_not_master_imports(self):
		import ast
		import inspect
		from nxtgen_savinda_pricing_calculator import hooks
		self.assertEqual(hooks.after_migrate, "nxtgen_savinda_pricing_calculator.install.after_migrate")
		self.assertEqual(hooks.before_migrate, "nxtgen_savinda_pricing_calculator.install.before_migrate")
		self.assertEqual(hooks.fixtures, [])
		calls = {n.func.id for n in ast.walk(ast.parse(inspect.getsource(install.after_migrate))) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
		self.assertIn("_ensure_custom_fields", calls)
		self.assertNotIn("_seed_install_only_fixtures", calls)


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
