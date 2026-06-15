# Copyright (c) 2025, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

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

	def test_bulk_job_migrates_all(self):
		set_multi_attachment_embed_enabled(False)

		sales_invoice_one = ensure_embed_test_sales_invoice()
		annex_one = create_embed_test_annex_file(
			file_name=f"bulk-migrate-one-{frappe.generate_hash(length=8)}.png",
		)
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice_one.name)
		self.addCleanup(delete_embed_test_annex_file, annex_one.name)
		annex_one.attached_to_doctype = "Sales Invoice"
		annex_one.attached_to_name = sales_invoice_one.name
		annex_one.save(ignore_permissions=True)
		frappe.db.set_value(
			"Sales Invoice",
			sales_invoice_one.name,
			"einvoice_embedded_document",
			annex_one.file_url,
		)

		sales_invoice_two = ensure_embed_test_sales_invoice()
		annex_two = create_embed_test_annex_file(
			file_name=f"bulk-migrate-two-{frappe.generate_hash(length=8)}.png",
		)
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice_two.name)
		self.addCleanup(delete_embed_test_annex_file, annex_two.name)
		annex_two.attached_to_doctype = "Sales Invoice"
		annex_two.attached_to_name = sales_invoice_two.name
		annex_two.save(ignore_permissions=True)
		frappe.db.set_value(
			"Sales Invoice",
			sales_invoice_two.name,
			"einvoice_embedded_document",
			annex_two.file_url,
		)

		set_multi_attachment_embed_enabled(True)

		result = bulk_migrate_legacy_embed_attachments()

		self.assertEqual(result["migrated"], 2)
		self.assertEqual(result["errors"], [])

		sales_invoice_one = frappe.get_doc("Sales Invoice", sales_invoice_one.name)
		sales_invoice_two = frappe.get_doc("Sales Invoice", sales_invoice_two.name)
		self.assertEqual(sales_invoice_one.einvoice_embedded_document, "")
		self.assertEqual(sales_invoice_two.einvoice_embedded_document, "")
		self.assertEqual(sales_invoice_one.einvoice_attachments[0].file, annex_one.name)
		self.assertEqual(sales_invoice_two.einvoice_attachments[0].file, annex_two.name)

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
