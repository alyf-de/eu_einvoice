# Copyright (c) 2025, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice_attachments import (
	LEGACY_EMBED_CUSTOM_FIELD,
	_format_bulk_migration_summary,
	bulk_migrate_legacy_embed_attachments,
	set_legacy_embed_field_lockdown,
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
		annex_file.save(ignore_permissions=True)
		frappe.db.set_value(
			"Sales Invoice",
			sales_invoice.name,
			"einvoice_embedded_document",
			annex_file.file_url,
		)

	if submit or cancel:
		sales_invoice.reload()
		with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_doc"):
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
		self._previous_custom_field = frappe.db.get_value(
			"Custom Field",
			LEGACY_EMBED_CUSTOM_FIELD,
			["hidden", "read_only"],
			as_dict=True,
		)

	def tearDown(self):
		set_multi_attachment_embed_enabled(bool(self._previous_setting))
		if self._previous_custom_field:
			custom_field = frappe.get_doc("Custom Field", LEGACY_EMBED_CUSTOM_FIELD)
			custom_field.hidden = self._previous_custom_field.hidden
			custom_field.read_only = self._previous_custom_field.read_only
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
						self.assertEqual(reloaded.einvoice_attachments[0].file, annex_file.name)
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
			self.assertEqual(reloaded.einvoice_attachments[0].file, annex_file.name)
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

	def test_field_lockdown_on_enable(self):
		set_legacy_embed_field_lockdown(False)

		set_multi_attachment_embed_enabled(True)

		custom_field = frappe.get_doc("Custom Field", LEGACY_EMBED_CUSTOM_FIELD)
		self.assertEqual(custom_field.hidden, 1)
		self.assertEqual(custom_field.read_only, 1)

		set_multi_attachment_embed_enabled(False)

		custom_field.reload()
		self.assertEqual(custom_field.hidden, 0)
		self.assertEqual(custom_field.read_only, 0)

	def test_create_custom_fields_respects_legacy_embed_lockdown(self):
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

		from eu_einvoice.custom_fields import get_custom_fields

		set_legacy_embed_field_lockdown(False)
		set_multi_attachment_embed_enabled(True)

		create_custom_fields(get_custom_fields())

		custom_field = frappe.get_doc("Custom Field", LEGACY_EMBED_CUSTOM_FIELD)
		self.assertEqual(custom_field.hidden, 1)
		self.assertEqual(custom_field.read_only, 1)
