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
	_insert_attachment_row,
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

	def test_bulk_migrate_migrates_all_docstatuses(self):
		set_multi_attachment_embed_enabled(True)

		for submit in (False, True):
			with self.subTest(submit=submit):
				sales_invoice, annex_file = create_invoice_with_legacy_embed(submit=submit)

				try:
					result = bulk_migrate_legacy_embed_attachments()

					self.assertEqual(result["errors"], [])
					self.assertEqual(result["migrated"], 1)

					reloaded = frappe.get_doc("Sales Invoice", sales_invoice.name)
					self.assertEqual(reloaded.einvoice_embedded_document, "")
					self.assertEqual(len(reloaded.einvoice_attachments), 1)
					migrated_file = frappe.db.get_value(
						"File", reloaded.einvoice_attachments[0].file, "file_url"
					)
					self.assertEqual(migrated_file, annex_file.file_url)
				finally:
					delete_embed_test_sales_invoice(sales_invoice.name)
					delete_embed_test_annex_file(annex_file.name)

	def test_bulk_migrate_assigns_child_row_idx(self):
		"""Direct child inserts must set ``idx`` so Desk grid order is stable."""
		set_multi_attachment_embed_enabled(True)
		sales_invoice, annex_file = create_invoice_with_legacy_embed(submit=True)
		second_annex_file = create_embed_test_annex_file(
			file_name=f"bulk-migrate-second-{frappe.generate_hash(length=8)}.png",
		)
		second_annex_file.attached_to_doctype = "Sales Invoice"
		second_annex_file.attached_to_name = sales_invoice.name
		second_annex_file.save(ignore_permissions=True)

		try:
			result = bulk_migrate_legacy_embed_attachments()
			self.assertEqual(result["errors"], [])
			self.assertEqual(result["migrated"], 1)

			rows = frappe.get_all(
				"E Invoice Attachment Row",
				filters={"parent": sales_invoice.name},
				fields=["idx", "file"],
				order_by="idx asc",
			)
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0].idx, 1)
			self.assertEqual(rows[0].file, annex_file.name)

			_insert_attachment_row(sales_invoice, second_annex_file)

			rows = frappe.get_all(
				"E Invoice Attachment Row",
				filters={"parent": sales_invoice.name},
				fields=["idx", "file"],
				order_by="idx asc",
			)
			self.assertEqual(len(rows), 2)
			self.assertEqual([row.idx for row in rows], [1, 2])
			self.assertEqual(rows[1].file, second_annex_file.name)
		finally:
			delete_embed_test_sales_invoice(sales_invoice.name)
			delete_embed_test_annex_file(annex_file.name)
			delete_embed_test_annex_file(second_annex_file.name)

	def test_bulk_migrate_idempotent_when_row_already_exists(self):
		"""Clear legacy without inserting when the File is already in the table."""
		set_multi_attachment_embed_enabled(True)
		sales_invoice, annex_file = create_invoice_with_legacy_embed(submit=True)
		_insert_attachment_row(sales_invoice, annex_file)

		real_get_all = frappe.get_all

		def get_all(doctype, *args, **kwargs):
			filters = kwargs.get("filters")
			if doctype == "Sales Invoice" and kwargs.get("pluck") == "name" and isinstance(filters, dict):
				if "einvoice_embedded_document" in filters:
					return [sales_invoice.name]
			return real_get_all(doctype, *args, **kwargs)

		try:
			with patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.frappe.get_all",
				side_effect=get_all,
			):
				result = bulk_migrate_legacy_embed_attachments()

			self.assertEqual(result["errors"], [])
			self.assertEqual(result["migrated"], 0)
			self.assertEqual(result["already_migrated"], 1)

			reloaded = frappe.get_doc("Sales Invoice", sales_invoice.name)
			self.assertEqual(reloaded.einvoice_embedded_document, "")
			self.assertEqual(len(reloaded.einvoice_attachments), 1)
			self.assertEqual(reloaded.einvoice_attachments[0].file, annex_file.name)
		finally:
			delete_embed_test_sales_invoice(sales_invoice.name)
			delete_embed_test_annex_file(annex_file.name)

	def test_bulk_migrate_cancelled_invoice(self):
		set_multi_attachment_embed_enabled(True)
		sales_invoice, annex_file = create_invoice_with_legacy_embed(submit=True, cancel=True)

		try:
			result = bulk_migrate_legacy_embed_attachments()

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
			result = bulk_migrate_legacy_embed_attachments()

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

	def test_on_update_queues_bulk_migration_when_multi_embed_enabled(self):
		settings = frappe.get_single("E Invoice Settings")
		settings.multi_attachment_embed_enabled = 0
		settings.save(ignore_permissions=True)

		with patch(
			"eu_einvoice.european_e_invoice.doctype.e_invoice_settings.e_invoice_settings.queue_bulk_migrate_legacy_embed_attachments"
		) as queue_mock:
			settings.multi_attachment_embed_enabled = 1
			settings.save(ignore_permissions=True)

		queue_mock.assert_called_once_with(enqueue_after_commit=True)

	def test_bulk_migration_publishes_progress_and_completion(self):
		set_multi_attachment_embed_enabled(True)
		sales_invoice, annex_file = create_invoice_with_legacy_embed(submit=True)
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		self.addCleanup(delete_embed_test_annex_file, annex_file.name)

		with patch(
			"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.frappe.publish_realtime"
		) as publish_mock:
			result = bulk_migrate_legacy_embed_attachments(notify_user="test@example.com")

		self.assertEqual(result["migrated"], 1)
		self.assertGreaterEqual(publish_mock.call_count, 1)
		completion_call = publish_mock.call_args_list[-1]
		self.assertIn("Migration finished", completion_call.args[1]["message"])
		self.assertEqual(completion_call.kwargs["user"], "test@example.com")
