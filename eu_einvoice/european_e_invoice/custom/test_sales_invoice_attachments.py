# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice import validate_doc
from eu_einvoice.european_e_invoice.custom.sales_invoice_attachments import (
	SALES_INVOICE_ATTACHMENT_TABLE_FIELD,
	deduplicate_attachment_rows,
)
from eu_einvoice.tests.helpers import (
	delete_embed_test_sales_invoice,
	ensure_embed_test_sales_invoice,
)


def append_attachment_rows(doc, rows: list[dict]) -> None:
	for row in rows:
		doc.append(SALES_INVOICE_ATTACHMENT_TABLE_FIELD, row)


def make_sales_invoice_doc(**kwargs) -> frappe.model.document.Document:
	doc = frappe.new_doc("Sales Invoice")
	doc.update(kwargs)
	return doc


class TestDeduplicateAttachmentRows(UnitTestCase):
	def test_deduplicate_first_wins(self):
		cases = [
			(
				"dedup_first_wins_two_rows",
				[
					{"file": "F-DEDUP-1", "display_name": "First"},
					{"file": "F-DEDUP-1", "display_name": "Last"},
				],
				1,
				["F-DEDUP-1"],
				["First"],
			),
			(
				"dedup_first_wins_three_rows",
				[
					{"file": "F-DEDUP-A", "display_name": "A"},
					{"file": "F-DEDUP-A", "display_name": "B"},
					{"file": "F-DEDUP-B", "display_name": "C"},
				],
				2,
				["F-DEDUP-A", "F-DEDUP-B"],
				["A", "C"],
			),
		]
		for name, attachment_rows, row_count, files, display_names in cases:
			with self.subTest(name=name):
				doc = make_sales_invoice_doc()
				append_attachment_rows(doc, attachment_rows)
				deduplicate_attachment_rows(doc)

				self.assertEqual(len(doc.einvoice_attachments), row_count)
				self.assertEqual([row.file for row in doc.einvoice_attachments], files)
				self.assertEqual(
					[row.display_name or "" for row in doc.einvoice_attachments],
					display_names,
				)


class IntegrationTestSalesInvoiceAttachments(IntegrationTestCase):
	def tearDown(self):
		super().tearDown()
		frappe.clear_messages()

	def test_validate_doc_deduplicates_attachment_rows(self):
		doc = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, doc.name)
		append_attachment_rows(
			doc,
			[
				{"file": "F-VALIDATE-1", "display_name": "First"},
				{"file": "F-VALIDATE-1", "display_name": "Last"},
			],
		)
		frappe.clear_messages()

		with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_einvoice"):
			validate_doc(doc, "validate")

		self.assertEqual(len(doc.einvoice_attachments), 1)
		self.assertEqual(doc.einvoice_attachments[0].display_name, "First")
		messages = frappe.get_message_log()
		dedup_messages = [message for message in messages if "removed" in message.message.lower()]
		self.assertEqual(len(dedup_messages), 1)
		self.assertEqual(dedup_messages[0].indicator, "orange")
