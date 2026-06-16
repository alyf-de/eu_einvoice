# Copyright (c) 2025, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice_attachments import (
	LEGACY_EMBED_CUSTOM_FIELD,
	bulk_migrate_legacy_embed_attachments,
	set_legacy_embed_field_lockdown,
)
from eu_einvoice.tests.helpers import (
	create_embed_test_annex_file,
	delete_embed_test_annex_file,
	delete_embed_test_sales_invoice,
	ensure_embed_test_sales_invoice,
)


def set_multi_attachment_embed_enabled(enabled: bool) -> None:
	settings = frappe.get_doc("E Invoice Settings")
	settings.multi_attachment_embed_enabled = 1 if enabled else 0
	settings.flags.ignore_permissions = True
	settings.save()


def create_invoice_with_legacy_embed(*, submit: bool = False) -> tuple[frappe.Document, frappe.Document]:
	sales_invoice = ensure_embed_test_sales_invoice()
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

	if submit:
		sales_invoice.reload()
		with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_doc"):
			sales_invoice.submit()

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
