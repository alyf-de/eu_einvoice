# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

import os
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.utils import find_file_by_url
from frappe.tests import IntegrationTestCase, UnitTestCase

from eu_einvoice.european_e_invoice.custom.embed_attachment_test_helpers import (
	assert_embed_attachment_result,
	load_embed_attachment_scenarios,
	make_embed_generator,
	mock_file_doc,
)
from eu_einvoice.european_e_invoice.custom.embed_attachment_test_helpers import (
	make_sales_invoice_doc as make_embed_test_invoice,
)
from eu_einvoice.european_e_invoice.custom.sales_invoice import as_base_64, validate_doc
from eu_einvoice.european_e_invoice.custom.sales_invoice_attachments import (
	deduplicate_attachment_rows,
	get_embed_attachments,
	get_legacy_embed_attachment,
	get_table_embed_attachments,
)
from eu_einvoice.tests.helpers import (
	LOCAL_ANNEX_PNG_BYTES,
	build_einvoice_generator,
	create_embed_test_annex_file,
	create_embed_test_sales_invoice,
	delete_embed_test_annex_file,
	delete_embed_test_sales_invoice,
	ensure_embed_test_sales_invoice,
)


def append_attachment_rows(doc, rows: list[dict]) -> None:
	for row in rows:
		doc.append("einvoice_attachments", row)


def make_sales_invoice_doc(**kwargs) -> frappe.model.document.Document:
	doc = frappe.new_doc("Sales Invoice")
	doc.update(kwargs)
	return doc


def set_multi_attachment_embed_enabled(enabled: bool) -> None:
	settings = frappe.get_doc("E Invoice Settings")
	settings.multi_attachment_embed_enabled = 1 if enabled else 0
	settings.flags.ignore_permissions = True
	settings.save()


def _file_url_lookup(file_name: str) -> str | None:
	return {
		"F-TABLE-1": "/files/table-annex-1.png",
		"F-TABLE-2": "/files/table-annex-2.pdf",
		"F-MISSING": None,
	}.get(file_name)


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

	def test_get_legacy_embed_attachment(self):
		for field_url, expected in (
			("", []),
			("/files/legacy-annex.png", ["/files/legacy-annex.png"]),
		):
			with self.subTest(field_url=field_url):
				invoice = make_embed_test_invoice(einvoice_embedded_document=field_url)
				self.assertEqual(get_legacy_embed_attachment(invoice), expected)

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


class UnitTestGetTableEmbedAttachments(UnitTestCase):
	def test_get_table_embed_attachments(self):
		cases = [
			("empty_table", [], []),
			(
				"grid_list_order_and_skip_empty",
				[
					frappe._dict(idx=2, file="F-TABLE-2"),
					frappe._dict(idx=1, file="F-TABLE-1"),
					frappe._dict(idx=3, file=""),
					frappe._dict(idx=4, file="F-MISSING"),
				],
				["/files/table-annex-2.pdf", "/files/table-annex-1.png"],
			),
		]
		for name, rows, expected in cases:
			with self.subTest(name=name):
				invoice = frappe._dict(einvoice_attachments=rows)
				with patch.object(
					frappe.db,
					"get_value",
					side_effect=lambda doctype, name, fieldname: _file_url_lookup(name),
				):
					self.assertEqual(get_table_embed_attachments(invoice), expected)


class UnitTestGetEmbedAttachments(UnitTestCase):
	def test_get_embed_attachments_branches_on_setting(self):
		legacy_invoice = make_embed_test_invoice(einvoice_embedded_document="/files/legacy.png")
		table_invoice = make_embed_test_invoice(
			einvoice_embedded_document="/files/legacy.png",
			einvoice_attachments=[frappe._dict(idx=1, file="F-TABLE-1")],
		)

		with patch.object(frappe.db, "get_single_value", return_value=0):
			self.assertEqual(get_embed_attachments(legacy_invoice), ["/files/legacy.png"])
			with patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.get_table_embed_attachments",
				return_value=["/files/table-only.png"],
			) as table_mock:
				self.assertEqual(get_embed_attachments(table_invoice), ["/files/legacy.png"])
				table_mock.assert_not_called()

		with patch.object(frappe.db, "get_single_value", return_value=1):
			with patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.get_table_embed_attachments",
				return_value=["/files/table-only.png"],
			) as table_mock:
				self.assertEqual(get_embed_attachments(table_invoice), ["/files/table-only.png"])
				table_mock.assert_called_once_with(table_invoice)

		with patch.object(frappe.db, "get_single_value", return_value=1):
			self.assertEqual(get_embed_attachments(legacy_invoice), [])


class UnitTestCreateEinvoiceEmbedSource(UnitTestCase):
	def test_table_embed_principles_match_legacy_scenarios(self):
		for scenario in load_embed_attachment_scenarios():
			if not scenario.field_url:
				continue
			with self.subTest(scenario=scenario.id):
				invoice = make_embed_test_invoice(
					einvoice_attachments=[frappe._dict(idx=1, file="F-TABLE-ROW")],
				)
				generator = make_embed_generator(invoice)
				mock_doc = mock_file_doc(scenario.mock_file) if scenario.mock_file else None

				with patch.object(frappe.db, "get_single_value", return_value=1):
					with patch(
						"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.frappe.db.get_value",
						return_value=scenario.field_url,
					):
						attachments = get_embed_attachments(invoice)

				with patch(
					"eu_einvoice.european_e_invoice.custom.sales_invoice.find_file_by_url",
					return_value=mock_doc,
				) as find_mock:
					generator._embed_attachments(attachments)
					find_mock.assert_called_once_with(scenario.field_url)

				mock_content = scenario.mock_file.content if scenario.mock_file else None
				assert_embed_attachment_result(
					generator,
					scenario.expect,
					mock_content=mock_content,
				)


class IntegrationTestSalesInvoiceAttachments(IntegrationTestCase):
	def tearDown(self):
		super().tearDown()
		frappe.clear_messages()

	def test_migrate_legacy_field_on_validate(self):
		self._previous_setting = frappe.db.get_single_value(
			"E Invoice Settings", "multi_attachment_embed_enabled"
		)
		self.addCleanup(set_multi_attachment_embed_enabled, bool(self._previous_setting))

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

		messages = frappe.get_message_log()
		migrate_messages = [message for message in messages if "moved" in message.message.lower()]
		self.assertEqual(len(migrate_messages), 1)
		self.assertEqual(migrate_messages[0].indicator, "orange")

	def test_migrate_legacy_field_keeps_broken_link_on_validate(self):
		self._previous_setting = frappe.db.get_single_value(
			"E Invoice Settings", "multi_attachment_embed_enabled"
		)
		self.addCleanup(set_multi_attachment_embed_enabled, bool(self._previous_setting))

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
		messages = frappe.get_message_log()
		dedup_messages = [message for message in messages if "removed" in message.message.lower()]
		self.assertEqual(len(dedup_messages), 1)
		self.assertEqual(dedup_messages[0].indicator, "orange")

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

	def test_create_einvoice_embeds_table_attachments_when_enabled(self):
		self._previous_setting = frappe.db.get_single_value(
			"E Invoice Settings", "multi_attachment_embed_enabled"
		)
		self.addCleanup(set_multi_attachment_embed_enabled, bool(self._previous_setting))

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
		sales_invoice.einvoice_embedded_document = annex_one.file_url
		append_attachment_rows(
			sales_invoice,
			[
				{"file": annex_two.name},
				{"file": annex_one.name},
			],
		)
		sales_invoice.save(ignore_permissions=True)
		sales_invoice.reload()

		set_multi_attachment_embed_enabled(True)

		self.assertEqual(
			get_table_embed_attachments(sales_invoice),
			[annex_two.file_url, annex_one.file_url],
		)

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

	def test_create_einvoice_uses_legacy_field_when_setting_off(self):
		self._previous_setting = frappe.db.get_single_value(
			"E Invoice Settings", "multi_attachment_embed_enabled"
		)
		self.addCleanup(set_multi_attachment_embed_enabled, bool(self._previous_setting))

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
