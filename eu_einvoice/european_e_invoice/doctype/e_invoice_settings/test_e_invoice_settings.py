# Copyright (c) 2025, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice_attachments import (
	LEGACY_EMBED_CUSTOM_FIELD,
	TABLE_EMBED_CUSTOM_FIELD,
	_format_bulk_migration_summary,
	bulk_migrate_legacy_embed_attachments,
	set_embed_attachment_field_exclusivity,
)
from eu_einvoice.tests.helpers import (
	create_embed_test_annex_file,
	delete_embed_test_annex_file,
	delete_embed_test_sales_invoice,
	ensure_embed_test_sales_invoice,
	set_multi_attachment_embed_enabled,
)


def create_invoice_with_legacy_embed(
	*,
	submit: bool = False,
	cancel: bool = False,
	broken_url: str | None = None,
) -> tuple[frappe.Document, frappe.Document | None]:
	sales_invoice = ensure_embed_test_sales_invoice()
	if broken_url:
		frappe.db.set_value(
			"Sales Invoice",
			sales_invoice.name,
			"einvoice_embedded_document",
			broken_url,
		)
		annex_file = None
	else:
		annex_file = create_embed_test_annex_file(
			file_name=f"bulk-migrate-{frappe.generate_hash(length=8)}.png",
		)
		annex_file.attached_to_doctype = "Sales Invoice"
		annex_file.attached_to_name = sales_invoice.name
		annex_file.attached_to_field = "einvoice_embedded_document"
		annex_file.save(ignore_permissions=True)
		frappe.db.set_value(
			"Sales Invoice",
			sales_invoice.name,
			"einvoice_embedded_document",
			annex_file.file_url,
		)

	if submit or cancel:
		sales_invoice.reload()
		with ExitStack() as stack:
			stack.enter_context(patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_doc"))
			if "pdf_on_submit" in frappe.get_installed_apps():
				# Avoid site PDF attach (wkhtmltopdf) when submitting fixtures for bulk migrate.
				stack.enter_context(patch("pdf_on_submit.attach_pdf.execute"))
			sales_invoice.submit()

	if cancel:
		sales_invoice.reload()
		sales_invoice.cancel()

	return sales_invoice, annex_file


class IntegrationTestEInvoiceSettings(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._previous_setting = frappe.db.get_single_value(
			"E Invoice Settings", "multi_attachment_embed_enabled"
		)
		self._previous_legacy_field = frappe.db.get_value(
			"Custom Field",
			LEGACY_EMBED_CUSTOM_FIELD,
			["hidden", "read_only"],
			as_dict=True,
		)
		self._previous_table_field = frappe.db.get_value(
			"Custom Field",
			TABLE_EMBED_CUSTOM_FIELD,
			["hidden"],
			as_dict=True,
		)

	def tearDown(self):
		set_multi_attachment_embed_enabled(bool(self._previous_setting))
		if self._previous_legacy_field:
			custom_field = frappe.get_doc("Custom Field", LEGACY_EMBED_CUSTOM_FIELD)
			custom_field.hidden = self._previous_legacy_field.hidden
			custom_field.read_only = self._previous_legacy_field.read_only
			custom_field.flags.ignore_permissions = True
			custom_field.save()
		if self._previous_table_field:
			custom_field = frappe.get_doc("Custom Field", TABLE_EMBED_CUSTOM_FIELD)
			custom_field.hidden = self._previous_table_field.hidden
			custom_field.flags.ignore_permissions = True
			custom_field.save()
		frappe.clear_cache(doctype="Sales Invoice")
		super().tearDown()

	def test_bulk_migrate_include_submitted_matrix(self):
		set_multi_attachment_embed_enabled(True)

		for submit, include_submitted, expect_migrated in (
			(False, False, True),
			(False, True, True),
			(True, False, False),
			(True, True, True),
		):
			with self.subTest(submit=submit, include_submitted=include_submitted):
				sales_invoice, annex_file = create_invoice_with_legacy_embed(submit=submit)

				try:
					result = bulk_migrate_legacy_embed_attachments(include_submitted=include_submitted)

					self.assertEqual(result["errors"], [])
					self.assertEqual(result["migrated"], 1 if expect_migrated else 0)

					reloaded = frappe.get_doc("Sales Invoice", sales_invoice.name)
					if expect_migrated:
						self.assertEqual(reloaded.einvoice_embedded_document, "")
						self.assertEqual(len(reloaded.einvoice_attachments), 1)
						migrated_file = frappe.db.get_value(
							"File", reloaded.einvoice_attachments[0].file, "file_url"
						)
						self.assertEqual(migrated_file, annex_file.file_url)
					else:
						self.assertEqual(reloaded.einvoice_embedded_document, annex_file.file_url)
						self.assertEqual(len(reloaded.einvoice_attachments), 0)
				finally:
					delete_embed_test_sales_invoice(sales_invoice.name)
					delete_embed_test_annex_file(annex_file.name)

	def test_bulk_migrate_cancelled_invoice_with_include_submitted(self):
		set_multi_attachment_embed_enabled(True)
		sales_invoice, annex_file = create_invoice_with_legacy_embed(submit=True, cancel=True)

		try:
			result = bulk_migrate_legacy_embed_attachments(include_submitted=True)

			self.assertEqual(result["errors"], [])
			self.assertEqual(result["migrated"], 1)

			reloaded = frappe.get_doc("Sales Invoice", sales_invoice.name)
			self.assertEqual(reloaded.einvoice_embedded_document, "")
			self.assertEqual(len(reloaded.einvoice_attachments), 1)
			migrated_file = frappe.db.get_value("File", reloaded.einvoice_attachments[0].file, "file_url")
			self.assertEqual(migrated_file, annex_file.file_url)
		finally:
			delete_embed_test_sales_invoice(sales_invoice.name)
			delete_embed_test_annex_file(annex_file.name)

	def test_bulk_migrate_skips_broken_legacy_link(self):
		set_multi_attachment_embed_enabled(True)
		broken_url = f"/files/missing-legacy-{frappe.generate_hash(length=8)}.png"
		sales_invoice, _ = create_invoice_with_legacy_embed(broken_url=broken_url)
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		legacy_embed_count = len(
			frappe.get_all(
				"Sales Invoice",
				filters={"einvoice_embedded_document": ("is", "set")},
			)
		)

		result = bulk_migrate_legacy_embed_attachments(remove_broken_links=False)

		self.assertEqual(result["errors"], [])
		self.assertEqual(result["migrated"], 0)
		self.assertEqual(result["broken"], legacy_embed_count)
		self.assertEqual(result.get("removed", 0), 0)
		self.assertEqual(
			frappe.db.get_value(
				"Sales Invoice",
				sales_invoice.name,
				"einvoice_embedded_document",
			),
			broken_url,
		)
		error_logs = frappe.get_all(
			"Error Log",
			filters={
				"reference_doctype": "Sales Invoice",
				"reference_name": sales_invoice.name,
				"error": ("like", f"%{broken_url}%"),
			},
			fields=["error"],
		)
		self.assertEqual(len(error_logs), 1)
		self.assertIn("left unchanged", error_logs[0].error.lower())

	def test_bulk_migrate_removes_broken_legacy_link(self):
		set_multi_attachment_embed_enabled(True)
		broken_url = f"/files/missing-legacy-{frappe.generate_hash(length=8)}.png"
		sales_invoice, _ = create_invoice_with_legacy_embed(broken_url=broken_url)
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		legacy_embed_count = len(
			frappe.get_all(
				"Sales Invoice",
				filters={"einvoice_embedded_document": ("is", "set")},
			)
		)

		with patch(
			"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.frappe.logger"
		) as mock_logger:
			result = bulk_migrate_legacy_embed_attachments(remove_broken_links=True)

		self.assertEqual(result["errors"], [])
		self.assertEqual(result["migrated"], 0)
		self.assertEqual(result["removed"], legacy_embed_count)
		self.assertEqual(result["broken"], 0)
		self.assertEqual(
			frappe.db.get_value(
				"Sales Invoice",
				sales_invoice.name,
				"einvoice_embedded_document",
			),
			"",
		)
		error_logs = frappe.get_all(
			"Error Log",
			filters={
				"reference_doctype": "Sales Invoice",
				"reference_name": sales_invoice.name,
				"error": ("like", f"%{broken_url}%"),
			},
		)
		self.assertEqual(error_logs, [])
		self.assertEqual(mock_logger.return_value.warning.call_count, legacy_embed_count)
		self.assertTrue(
			any(
				broken_url in str(call.args) and "removed" in str(call.args).lower()
				for call in mock_logger.return_value.warning.call_args_list
			)
		)

	def test_bulk_migrate_rolls_back_partial_db_persist(self):
		"""Failed clear after child insert must not leave an orphaned attachment row."""
		set_multi_attachment_embed_enabled(True)
		fail_invoice, fail_file = create_invoice_with_legacy_embed(submit=True)
		ok_invoice, ok_file = create_invoice_with_legacy_embed(submit=True)

		def _cleanup_committed_fixture(sales_invoice_name: str, annex_file_name: str) -> None:
			delete_embed_test_sales_invoice(sales_invoice_name)
			delete_embed_test_annex_file(annex_file_name)
			frappe.db.commit()  # nosemgrep

		self.addCleanup(_cleanup_committed_fixture, fail_invoice.name, fail_file.name)
		self.addCleanup(_cleanup_committed_fixture, ok_invoice.name, ok_file.name)
		# Persist fixtures so the migrate except-path rollback cannot undo them.
		frappe.db.commit()  # nosemgrep

		real_get_all = frappe.get_all
		real_set_value = frappe.db.set_value

		def get_all(doctype, *args, **kwargs):
			filters = kwargs.get("filters")
			if doctype == "Sales Invoice" and kwargs.get("pluck") == "name" and isinstance(filters, dict):
				if "einvoice_embedded_document" in filters:
					return [fail_invoice.name, ok_invoice.name]
			return real_get_all(doctype, *args, **kwargs)

		def set_value(doctype, docname=None, fieldname=None, value=None, *args, **kwargs):
			if (
				doctype == "Sales Invoice"
				and docname == fail_invoice.name
				and fieldname == "einvoice_embedded_document"
				and value == ""
			):
				raise RuntimeError("simulated lock while clearing legacy embed")
			return real_set_value(doctype, docname, fieldname, value, *args, **kwargs)

		with (
			patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.frappe.get_all",
				side_effect=get_all,
			),
			patch.object(frappe.db, "set_value", side_effect=set_value),
		):
			result = bulk_migrate_legacy_embed_attachments(include_submitted=True)

		self.assertEqual(result["migrated"], 1)
		self.assertEqual(len(result["errors"]), 1)
		self.assertEqual(result["errors"][0][0], fail_invoice.name)

		fail_reloaded = frappe.get_doc("Sales Invoice", fail_invoice.name)
		self.assertEqual(fail_reloaded.einvoice_embedded_document, fail_file.file_url)
		self.assertEqual(
			frappe.db.count("E Invoice Attachment Row", {"parent": fail_invoice.name}),
			0,
		)

		ok_reloaded = frappe.get_doc("Sales Invoice", ok_invoice.name)
		self.assertEqual(ok_reloaded.einvoice_embedded_document, "")
		self.assertEqual(len(ok_reloaded.einvoice_attachments), 1)

	def test_bulk_migration_summary_segments(self):
		summary = _format_bulk_migration_summary(
			migrated=2,
			already_migrated=1,
			broken=3,
			removed=1,
			errors=[("SINV-ERR", "boom")],
		)

		self.assertIn("Migrated 2", summary)
		self.assertIn("Already migrated 1", summary)
		self.assertIn("Broken file links (skipped): 3", summary)
		self.assertIn("Broken file links (removed): 1", summary)
		self.assertIn("Errors: 1", summary)

	def test_field_exclusivity_on_enable(self):
		set_embed_attachment_field_exclusivity(False)

		set_multi_attachment_embed_enabled(True)

		legacy_field = frappe.get_doc("Custom Field", LEGACY_EMBED_CUSTOM_FIELD)
		self.assertEqual(legacy_field.hidden, 1)
		self.assertEqual(legacy_field.read_only, 1)
		table_field = frappe.get_doc("Custom Field", TABLE_EMBED_CUSTOM_FIELD)
		self.assertEqual(table_field.hidden, 0)

		set_multi_attachment_embed_enabled(False)

		legacy_field.reload()
		self.assertEqual(legacy_field.hidden, 0)
		self.assertEqual(legacy_field.read_only, 0)
		table_field.reload()
		self.assertEqual(table_field.hidden, 1)

	def test_create_custom_fields_respects_embed_field_exclusivity(self):
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

		from eu_einvoice.custom_fields import get_custom_fields

		set_embed_attachment_field_exclusivity(False)
		set_multi_attachment_embed_enabled(True)

		create_custom_fields(get_custom_fields())

		legacy_field = frappe.get_doc("Custom Field", LEGACY_EMBED_CUSTOM_FIELD)
		self.assertEqual(legacy_field.hidden, 1)
		self.assertEqual(legacy_field.read_only, 1)
		table_field = frappe.get_doc("Custom Field", TABLE_EMBED_CUSTOM_FIELD)
		self.assertEqual(table_field.hidden, 0)

	def test_after_migrate_syncs_embed_field_exclusivity(self):
		from eu_einvoice.install import after_migrate

		set_embed_attachment_field_exclusivity(False)
		frappe.db.set_single_value("E Invoice Settings", "multi_attachment_embed_enabled", 1)

		after_migrate()

		legacy_field = frappe.get_doc("Custom Field", LEGACY_EMBED_CUSTOM_FIELD)
		self.assertEqual(legacy_field.hidden, 1)
		self.assertEqual(legacy_field.read_only, 1)
		table_field = frappe.get_doc("Custom Field", TABLE_EMBED_CUSTOM_FIELD)
		self.assertEqual(table_field.hidden, 0)
