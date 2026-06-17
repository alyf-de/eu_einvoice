# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

import base64
import os
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.utils import find_file_by_url
from frappe.tests import IntegrationTestCase, UnitTestCase

from eu_einvoice.european_e_invoice.custom.embed_attachment_test_helpers import (
	assert_embed_attachment_result,
	extract_attachment_binary_objects_from_cii_xml,
	load_embed_attachment_scenarios,
	make_embed_generator,
	make_minimal_pdf_bytes,
	mock_file_doc,
)
from eu_einvoice.european_e_invoice.custom.embed_attachment_test_helpers import (
	make_sales_invoice_doc as make_embed_test_invoice,
)
from eu_einvoice.european_e_invoice.custom.sales_invoice import (
	as_base_64,
	attach_xml_to_pdf,
	get_einvoice,
	validate_doc,
)
from eu_einvoice.european_e_invoice.custom.sales_invoice_attachments import (
	get_embed_attachments,
	get_table_embed_attachments,
)
from eu_einvoice.tests.helpers import (
	LOCAL_ANNEX_PNG_BYTES,
	assert_single_orange_message,
	build_einvoice_generator,
	create_embed_test_annex_file,
	create_embed_test_sales_invoice,
	delete_embed_test_annex_file,
	delete_embed_test_sales_invoice,
	ensure_embed_test_sales_invoice,
	set_multi_attachment_embed_enabled,
)


def append_attachment_rows(doc, rows: list[dict]) -> None:
	for row in rows:
		doc.append("einvoice_attachments", row)


class UnitTestLegacyEmbedAttachment(UnitTestCase):
	def test_invalid_file_url_on_embed(self):
		generator = make_embed_generator(make_embed_test_invoice())
		with patch(
			"eu_einvoice.european_e_invoice.custom.sales_invoice.find_file_by_url",
			return_value=None,
		):
			with self.assertRaises(frappe.ValidationError) as error:
				generator._embed_attachments(["/files/missing-annex.png"])

		self.assertIn("/files/missing-annex.png", str(error.exception))
		self.assertIn("File record", str(error.exception))

	def test_embed_attachments_scenarios(self):
		for scenario in load_embed_attachment_scenarios():
			with self.subTest(scenario=scenario.id):
				generator = make_embed_generator(make_embed_test_invoice())
				attachments = [scenario.field_url] if scenario.field_url else []

				mock_doc = mock_file_doc(scenario.mock_file) if scenario.mock_file else None
				with patch(
					"eu_einvoice.european_e_invoice.custom.sales_invoice.find_file_by_url",
					return_value=mock_doc,
				) as find_mock:
					generator._embed_attachments(attachments)
					if scenario.mock_file:
						find_mock.assert_called_once_with(scenario.field_url)
					else:
						find_mock.assert_not_called()

				mock_content = scenario.mock_file.content if scenario.mock_file else None
				assert_embed_attachment_result(
					generator,
					scenario.expect,
					mock_content=mock_content,
				)


class UnitTestGetEmbedAttachments(UnitTestCase):
	def test_get_embed_attachments_branches_on_setting(self):
		legacy_invoice = make_embed_test_invoice(einvoice_embedded_document="/files/legacy.png")
		table_invoice = make_embed_test_invoice(
			einvoice_embedded_document="/files/legacy.png",
			einvoice_attachments=[frappe._dict(idx=1, file="F-TABLE-1")],
		)

		for case, setting_enabled, invoice, expected, mock_table_urls in (
			("legacy_when_off", False, legacy_invoice, ["/files/legacy.png"], None),
			("table_ignored_when_off", False, table_invoice, ["/files/legacy.png"], None),
			("table_when_on", True, table_invoice, ["/files/table-only.png"], ["/files/table-only.png"]),
			("empty_table_when_on", True, legacy_invoice, [], None),
		):
			with self.subTest(case=case):
				with patch.object(frappe.db, "get_single_value", return_value=1 if setting_enabled else 0):
					if mock_table_urls is not None:
						with patch(
							"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.get_table_embed_attachments",
							return_value=mock_table_urls,
						) as table_mock:
							self.assertEqual(get_embed_attachments(invoice), expected)
							table_mock.assert_called_once_with(invoice)
					elif setting_enabled:
						self.assertEqual(get_embed_attachments(invoice), expected)
					else:
						with patch(
							"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.get_table_embed_attachments",
						) as table_mock:
							self.assertEqual(get_embed_attachments(invoice), expected)
							table_mock.assert_not_called()

	def test_get_table_embed_attachments_raises_when_file_url_missing(self):
		invoice = make_embed_test_invoice(
			einvoice_attachments=[frappe._dict(idx=1, file="F-MISSING-URL", file_name="Missing URL")],
		)
		with patch(
			"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.frappe.db.get_value",
			return_value=None,
		):
			with self.assertRaises(frappe.ValidationError) as error:
				get_table_embed_attachments(invoice)

		self.assertIn("Missing URL", str(error.exception))
		self.assertIn("ID F-MISSING-URL", str(error.exception))
		self.assertIn("file URL", str(error.exception))


class IntegrationTestSalesInvoiceAttachments(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._previous_setting = frappe.db.get_single_value(
			"E Invoice Settings", "multi_attachment_embed_enabled"
		)

	def tearDown(self):
		set_multi_attachment_embed_enabled(bool(self._previous_setting))
		frappe.clear_messages()
		super().tearDown()

	def test_migrate_legacy_field_on_validate(self):
		set_multi_attachment_embed_enabled(False)
		sales_invoice, annex_file = create_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		self.addCleanup(delete_embed_test_annex_file, annex_file.name)

		set_multi_attachment_embed_enabled(True)
		frappe.clear_messages()

		with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_einvoice"):
			validate_doc(sales_invoice, "validate")

		self.assertEqual(sales_invoice.einvoice_embedded_document, "")
		self.assertEqual(len(sales_invoice.einvoice_attachments), 1)
		self.assertEqual(sales_invoice.einvoice_attachments[0].file, annex_file.name)
		assert_single_orange_message("moved")

	def test_migrate_legacy_field_keeps_broken_link_on_validate(self):
		set_multi_attachment_embed_enabled(True)
		sales_invoice = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		broken_url = f"/files/missing-legacy-{frappe.generate_hash(length=8)}.png"
		sales_invoice.einvoice_embedded_document = broken_url
		frappe.clear_messages()

		with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_einvoice"):
			validate_doc(sales_invoice, "validate")

		self.assertEqual(sales_invoice.einvoice_embedded_document, broken_url)
		self.assertEqual(len(sales_invoice.einvoice_attachments), 0)

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
		self.assertIn(sales_invoice.name, error_logs[0].error)

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
		assert_single_orange_message("removed")

	def test_create_einvoice_embeds_legacy_attachment(self):
		sales_invoice, annex_file = create_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		self.addCleanup(delete_embed_test_annex_file, annex_file.name)

		generator = build_einvoice_generator(sales_invoice)
		generator.create_einvoice()

		resolved_file = find_file_by_url(annex_file.file_url)
		refs = generator.doc.trade.agreement.additional_references.children
		self.assertEqual(len(refs), 1)

		ref = refs[0]
		self.assertEqual(str(ref.type_code), "916")
		self.assertEqual(ref.issuer_assigned_id._text, resolved_file.name)

		attached_object = ref.attached_object
		self.assertEqual(attached_object._mime_code, "image/png")
		self.assertEqual(attached_object._filename, annex_file.file_name)
		self.assertEqual(attached_object._text, as_base_64(resolved_file.get_content()))

	def test_get_table_embed_attachments_returns_urls_in_row_order(self):
		set_multi_attachment_embed_enabled(True)

		annex_one = create_embed_test_annex_file(
			file_name=f"table-order-1-{frappe.generate_hash(length=8)}.png",
			content=LOCAL_ANNEX_PNG_BYTES + b"1",
		)
		annex_two = create_embed_test_annex_file(
			file_name=f"table-order-2-{frappe.generate_hash(length=8)}.png",
			content=LOCAL_ANNEX_PNG_BYTES + b"2",
		)
		self.addCleanup(delete_embed_test_annex_file, annex_one.name)
		self.addCleanup(delete_embed_test_annex_file, annex_two.name)

		sales_invoice = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		append_attachment_rows(
			sales_invoice,
			[
				{"file": annex_two.name},
				{"file": annex_one.name},
			],
		)
		sales_invoice.save(ignore_permissions=True)
		sales_invoice.reload()

		self.assertEqual(
			get_table_embed_attachments(sales_invoice),
			[annex_two.file_url, annex_one.file_url],
		)

	def test_create_einvoice_embeds_table_attachments_when_enabled(self):
		set_multi_attachment_embed_enabled(True)

		annex_one = create_embed_test_annex_file(
			file_name=f"table-embed-1-{frappe.generate_hash(length=8)}.png",
			content=LOCAL_ANNEX_PNG_BYTES + b"1",
		)
		annex_two = create_embed_test_annex_file(
			file_name=f"table-embed-2-{frappe.generate_hash(length=8)}.png",
			content=LOCAL_ANNEX_PNG_BYTES + b"2",
		)
		self.addCleanup(delete_embed_test_annex_file, annex_one.name)
		self.addCleanup(delete_embed_test_annex_file, annex_two.name)

		sales_invoice = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		append_attachment_rows(
			sales_invoice,
			[
				{"file": annex_two.name},
				{"file": annex_one.name},
			],
		)
		sales_invoice.save(ignore_permissions=True)
		sales_invoice.reload()

		generator = build_einvoice_generator(sales_invoice)
		generator.create_einvoice()

		refs = generator.doc.trade.agreement.additional_references.children
		self.assertEqual(len(refs), 2)
		self.assertEqual([str(ref.type_code) for ref in refs], ["916", "916"])
		self.assertEqual(
			[ref.issuer_assigned_id._text for ref in refs],
			[annex_two.name, annex_one.name],
		)

		for ref, annex_file in zip(refs, (annex_two, annex_one), strict=True):
			resolved_file = find_file_by_url(annex_file.file_url)
			self.assertEqual(ref.attached_object._filename, os.path.basename(resolved_file.file_url))
			self.assertEqual(ref.attached_object._text, as_base_64(resolved_file.get_content()))

	def test_attach_xml_to_pdf_embeds_table_attachment_content(self):
		from facturx import get_xml_from_pdf

		set_multi_attachment_embed_enabled(True)

		annex_content = LOCAL_ANNEX_PNG_BYTES + b"-pdf-roundtrip"
		annex_file = create_embed_test_annex_file(
			file_name=f"pdf-roundtrip-{frappe.generate_hash(length=8)}.png",
			content=annex_content,
		)
		self.addCleanup(delete_embed_test_annex_file, annex_file.name)

		sales_invoice = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		append_attachment_rows(sales_invoice, [{"file": annex_file.name}])
		sales_invoice.save(ignore_permissions=True)
		sales_invoice.reload()

		invoice_xml = get_einvoice(sales_invoice.name)
		expected_annexes = extract_attachment_binary_objects_from_cii_xml(invoice_xml)
		expected = next(annex for annex in expected_annexes if annex[0] == annex_file.file_name)
		self.assertTrue(expected[2])
		self.assertIn(b"-pdf-roundtrip", base64.b64decode(expected[2]))

		hybrid_pdf = attach_xml_to_pdf(sales_invoice.name, make_minimal_pdf_bytes())
		_, embedded_xml = get_xml_from_pdf(hybrid_pdf, check_xsd=False)

		pdf_annexes = extract_attachment_binary_objects_from_cii_xml(embedded_xml)
		self.assertIn(expected, pdf_annexes)

	def test_create_einvoice_uses_legacy_field_when_setting_off(self):
		set_multi_attachment_embed_enabled(False)
		sales_invoice, annex_file = create_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		self.addCleanup(delete_embed_test_annex_file, annex_file.name)

		other_annex = create_embed_test_annex_file(
			file_name=f"table-ignored-{frappe.generate_hash(length=8)}.png",
			content=LOCAL_ANNEX_PNG_BYTES + b"ignored",
		)
		self.addCleanup(delete_embed_test_annex_file, other_annex.name)

		append_attachment_rows(sales_invoice, [{"file": other_annex.name}])
		sales_invoice.save(ignore_permissions=True)
		sales_invoice.reload()

		generator = build_einvoice_generator(sales_invoice)
		generator.create_einvoice()

		refs = generator.doc.trade.agreement.additional_references.children
		self.assertEqual(len(refs), 1)
		self.assertEqual(refs[0].issuer_assigned_id._text, annex_file.name)
