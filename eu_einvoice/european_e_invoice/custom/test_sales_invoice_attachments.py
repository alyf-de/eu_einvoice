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
	MockFileSpec,
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
	EmbedAttachment,
	_get_table_embed_attachments,
	get_embed_attachments,
	validate_einvoice_attachment_rows,
)
from eu_einvoice.tests.helpers import (
	LOCAL_ANNEX_PNG_BYTES,
	assert_single_orange_message,
	attach_embed_test_annex_file_to_sales_invoice,
	build_einvoice_generator,
	create_embed_test_annex_file,
	create_embed_test_sales_invoice,
	delete_embed_test_annex_file,
	delete_embed_test_sales_invoice,
	ensure_embed_test_sales_invoice,
	set_multi_attachment_embed_enabled,
)


def shared_storage_file_mocks(*, shared_url: str, content: bytes) -> tuple[frappe._dict, frappe._dict]:
	"""Two **File**-shaped mocks: same ``file_url``, different ``file_name`` / ``name``."""
	alpha = mock_file_doc(MockFileSpec("F-DUP-A", shared_url, False, content))
	alpha.file_name = "dup-alpha.png"
	beta = mock_file_doc(MockFileSpec("F-DUP-B", shared_url, False, content))
	beta.file_name = "dup-beta.png"
	return alpha, beta


def append_attachment_rows(doc, rows: list[dict]) -> None:
	for row in rows:
		doc.append("einvoice_attachments", row)


def run_embed_with_attachments(generator, attachments: list[EmbedAttachment]) -> None:
	with patch(
		"eu_einvoice.european_e_invoice.custom.sales_invoice.get_embed_attachments",
		return_value=attachments,
	):
		generator._embed_attachments()


class UnitTestGetTableEmbedAttachmentsValidation(UnitTestCase):
	def test_raises_when_file_missing(self):
		invoice = make_embed_test_invoice(
			name="SINV-UNIT-MISSING",
			einvoice_attachments=[frappe._dict(idx=1, file="F-MISSING", file_name="missing-annex.png")],
		)
		with patch.object(frappe.db, "get_value", return_value=None):
			with self.assertRaises(frappe.ValidationError) as error:
				_get_table_embed_attachments(invoice)

		self.assertIn("missing-annex.png", str(error.exception))
		self.assertIn("F-MISSING", str(error.exception))
		self.assertIn("File record", str(error.exception))

	def test_raises_when_file_not_attached_to_invoice(self):
		invoice = make_embed_test_invoice(
			name="SINV-UNIT-FOREIGN",
			einvoice_attachments=[frappe._dict(idx=1, file="F-FOREIGN", file_name="foreign-annex.png")],
		)
		with patch.object(
			frappe.db,
			"get_value",
			return_value=("Sales Invoice", "SINV-OTHER"),
		):
			with self.assertRaises(frappe.ValidationError) as error:
				_get_table_embed_attachments(invoice)

		self.assertIn("foreign-annex.png", str(error.exception))
		self.assertIn("F-FOREIGN", str(error.exception))
		self.assertIn("attached to this document", str(error.exception).lower())

	def test_raises_on_duplicate_filename(self):
		for file_names in (("same.pdf", "same.pdf"), ("doc.pdf", "Doc.pdf")):
			with self.subTest(file_names=file_names):
				invoice = make_embed_test_invoice(
					name="SINV-UNIT-DUP",
					einvoice_attachments=[
						frappe._dict(idx=1, file="F-1", file_name=file_names[0]),
						frappe._dict(idx=2, file="F-2", file_name=file_names[1]),
					],
				)
				with patch.object(
					frappe.db,
					"get_value",
					return_value=("Sales Invoice", "SINV-UNIT-DUP"),
				):
					with self.assertRaises(frappe.ValidationError) as error:
						_get_table_embed_attachments(invoice)

				self.assertIn("unique filename", str(error.exception).lower())
				self.assertIn("row #1", str(error.exception))
				self.assertIn("row #2", str(error.exception))
				self.assertIn("same filename", str(error.exception).lower())


class UnitTestLegacyEmbedAttachment(UnitTestCase):
	def test_embed_attachments_scenarios(self):
		for scenario in load_embed_attachment_scenarios():
			with self.subTest(scenario=scenario.id):
				generator = make_embed_generator(make_embed_test_invoice())
				attachments = []
				if scenario.field_url:
					attachments = [
						EmbedAttachment(
							file=scenario.mock_file.name,
							file_name=scenario.expect.filename or scenario.mock_file.name,
						)
					]

				mock_doc = mock_file_doc(scenario.mock_file) if scenario.mock_file else None
				with (
					patch(
						"eu_einvoice.european_e_invoice.custom.sales_invoice.frappe.db.exists",
						return_value=True,
					),
					patch(
						"eu_einvoice.european_e_invoice.custom.sales_invoice.frappe.get_doc",
						return_value=mock_doc,
					) as get_doc_mock,
				):
					run_embed_with_attachments(generator, attachments)
					if scenario.mock_file:
						get_doc_mock.assert_called_once_with("File", scenario.mock_file.name)
					else:
						get_doc_mock.assert_not_called()

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
			einvoice_embedded_document="",
			einvoice_attachments=[frappe._dict(idx=1, file="F-TABLE-1", file_name="table-only.png")],
		)
		legacy_result = [EmbedAttachment(file="F-LEGACY", file_name="legacy.png")]
		table_result = [EmbedAttachment(file="F-TABLE-1", file_name="table-only.png")]

		for case, setting_enabled, invoice, expected, source in (
			("legacy_when_off", False, legacy_invoice, legacy_result, "legacy"),
			("table_ignored_when_off", False, table_invoice, legacy_result, "legacy"),
			("table_when_on", True, table_invoice, table_result, "table"),
		):
			with self.subTest(case=case):
				with patch.object(frappe.db, "get_single_value", return_value=1 if setting_enabled else 0):
					if source == "table":
						with patch(
							"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments._get_table_embed_attachments",
							return_value=expected,
						) as table_mock:
							self.assertEqual(get_embed_attachments(invoice), expected)
							table_mock.assert_called_once_with(invoice)
					else:
						with (
							patch(
								"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments._get_legacy_embed_attachment",
								return_value=expected,
							) as legacy_mock,
							patch(
								"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments._get_table_embed_attachments",
							) as table_mock,
						):
							self.assertEqual(get_embed_attachments(invoice), expected)
							legacy_mock.assert_called_once_with(invoice)
							table_mock.assert_not_called()

	def test_get_embed_attachments_raises_when_legacy_unmigrated_and_setting_on(self):
		legacy_invoice = make_embed_test_invoice(einvoice_embedded_document="/files/legacy.png")
		with patch.object(frappe.db, "get_single_value", return_value=1):
			with self.assertRaises(frappe.ValidationError) as error:
				get_embed_attachments(legacy_invoice)

		self.assertIn("not been migrated", str(error.exception))
		self.assertIn("E Invoice Settings", str(error.exception))

	def test_get_embed_attachments_uses_table_when_legacy_set_and_table_has_rows(self):
		invoice = make_embed_test_invoice(
			einvoice_embedded_document="/files/legacy.png",
			einvoice_attachments=[frappe._dict(idx=1, file="F-TABLE-1", file_name="table-only.png")],
		)
		table_result = [EmbedAttachment(file="F-TABLE-1", file_name="table-only.png")]
		with (
			patch.object(frappe.db, "get_single_value", return_value=1),
			patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments._get_table_embed_attachments",
				return_value=table_result,
			) as table_mock,
			patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments._get_legacy_embed_attachment",
			) as legacy_mock,
		):
			self.assertEqual(get_embed_attachments(invoice), table_result)
			table_mock.assert_called_once_with(invoice)
			legacy_mock.assert_not_called()

	def test_get_table_embed_attachments_raises_when_file_missing(self):
		invoice = make_embed_test_invoice(
			name="SINV-UNIT-MISSING-2",
			einvoice_attachments=[frappe._dict(idx=1, file="F-MISSING", file_name="Missing File")],
		)
		with patch.object(frappe.db, "get_value", return_value=None):
			with self.assertRaises(frappe.ValidationError) as error:
				_get_table_embed_attachments(invoice)

		self.assertIn("Missing File", str(error.exception))
		self.assertIn("ID F-MISSING", str(error.exception))
		self.assertIn("File record", str(error.exception))


class UnitTestValidateEinvoiceAttachmentRows(UnitTestCase):
	def test_duplicate_content_hash_shows_orange_warning(self):
		"""Identical ``content_hash`` across rows is a warn-only user-error hint."""
		invoice = make_embed_test_invoice(
			name="SINV-UNIT-HASH",
			einvoice_attachments=[
				frappe._dict(idx=1, file="F-DUP-A", file_name="alpha.png", display_name=None),
				frappe._dict(idx=2, file="F-DUP-B", file_name="beta.png", display_name=None),
			],
		)
		file_names = {"F-DUP-A": "alpha.png", "F-DUP-B": "beta.png"}

		def mock_get_value(doctype, name=None, fieldname=None, *args, **kwargs):
			if doctype == "File" and isinstance(fieldname, tuple):
				return ("Sales Invoice", "SINV-UNIT-HASH")
			if doctype == "File" and fieldname == "content_hash":
				return "shared-content-hash"
			if doctype == "File" and fieldname == "file_name":
				return file_names[name]
			return None

		settings = frappe._dict(
			should_raise_exception=lambda docstatus: False,
			should_show_message=lambda docstatus: False,
		)
		frappe.clear_messages()
		with patch.object(frappe.db, "get_value", side_effect=mock_get_value):
			validate_einvoice_attachment_rows(invoice, settings)

		duplicate_warnings = [
			message
			for message in frappe.get_message_log()
			if message.indicator == "orange" and "identical file content" in message.message.lower()
		]
		self.assertEqual(len(duplicate_warnings), 1)
		warning_message = duplicate_warnings[0].message
		self.assertIn("row #1", warning_message)
		self.assertIn("row #2", warning_message)
		self.assertIn("alpha.png", warning_message)
		self.assertIn("beta.png", warning_message)

	def test_duplicate_embed_filename_raises(self):
		for file_names in (("same.pdf", "same.pdf"), ("doc.pdf", "Doc.pdf")):
			with self.subTest(file_names=file_names):
				invoice = make_embed_test_invoice(
					name="SINV-UNIT-VALIDATE-DUP",
					einvoice_attachments=[
						frappe._dict(idx=1, file="F-1", file_name=file_names[0], display_name=None),
						frappe._dict(idx=2, file="F-2", file_name=file_names[1], display_name=None),
					],
				)
				settings = frappe._dict()

				with self.assertRaises(frappe.ValidationError) as error:
					validate_einvoice_attachment_rows(invoice, settings)

				self.assertIn("unique filename", str(error.exception).lower())
				self.assertIn("row #1", str(error.exception))
				self.assertIn("row #2", str(error.exception))

	def test_foreign_embed_file_raises(self):
		invoice = make_embed_test_invoice(
			name="SINV-UNIT-VALIDATE-FOREIGN",
			einvoice_attachments=[
				frappe._dict(idx=1, file="F-FOREIGN", file_name="foreign.pdf", display_name=None),
			],
		)
		settings = frappe._dict()

		def mock_get_value(doctype, name=None, fieldname=None, *args, **kwargs):
			if doctype == "File" and name == "F-FOREIGN":
				return ("Sales Invoice", "SINV-OTHER")
			return None

		with patch.object(frappe.db, "get_value", side_effect=mock_get_value):
			with self.assertRaises(frappe.ValidationError) as error:
				validate_einvoice_attachment_rows(invoice, settings)

		self.assertIn("foreign.pdf", str(error.exception))
		self.assertIn("attached to this document", str(error.exception).lower())

	def test_duplicate_empty_embed_filename_raises(self):
		invoice = make_embed_test_invoice(
			einvoice_attachments=[
				frappe._dict(idx=1, file="F-1", file_name="", display_name=None),
				frappe._dict(idx=2, file="F-2", file_name="", display_name=None),
			],
		)
		settings = frappe._dict()

		with self.assertRaises(frappe.ValidationError) as error:
			validate_einvoice_attachment_rows(invoice, settings)

		self.assertIn("unique filename", str(error.exception).lower())

	def test_embed_attachments_distinct_916_properties_for_shared_file_url(self):
		"""BR-52 + BR-DE-22: one ARD 916 per row; distinct IssuerAssignedID and @filename."""
		shared_content = LOCAL_ANNEX_PNG_BYTES + b"-dup-content"
		alpha, beta = shared_storage_file_mocks(
			shared_url="/files/shared-dedup-storage.png", content=shared_content
		)

		generator = make_embed_generator(make_embed_test_invoice())
		attachments = [
			EmbedAttachment(file="F-DUP-A", file_name="dup-alpha.png"),
			EmbedAttachment(file="F-DUP-B", file_name="dup-beta.png"),
		]
		with (
			patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice.frappe.db.exists",
				return_value=True,
			),
			patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice.frappe.get_doc",
				side_effect=[alpha, beta],
			),
		):
			run_embed_with_attachments(generator, attachments)

		refs = generator.doc.trade.agreement.additional_references.children
		self.assertEqual(len(refs), 2)
		self.assertEqual([str(ref.type_code) for ref in refs], ["916", "916"])

		issuer_ids = {ref.issuer_assigned_id._text for ref in refs}
		filenames = {ref.attached_object._filename for ref in refs}
		self.assertEqual(issuer_ids, {"F-DUP-A", "F-DUP-B"})
		self.assertEqual(filenames, {"dup-alpha.png", "dup-beta.png"})

		expected_payload = as_base_64(shared_content)
		for ref in refs:
			self.assertEqual(ref.attached_object._text, expected_payload)


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

		assert_single_orange_message(broken_url)
		warning = next(message for message in frappe.get_message_log() if broken_url in message.message)
		self.assertIn("einvoice_embedded_document", warning.message)
		self.assertIn("System Manager", warning.message)
		self.assertIn("E Invoice Settings", warning.message)

	def test_migrate_legacy_field_clears_broken_link_when_table_has_rows(self):
		set_multi_attachment_embed_enabled(True)
		sales_invoice = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)

		annex_file_name = f"table-annex-{frappe.generate_hash(length=8)}.png"
		annex_content = LOCAL_ANNEX_PNG_BYTES + frappe.generate_hash(length=8).encode()
		annex_file = create_embed_test_annex_file(file_name=annex_file_name, content=annex_content)
		self.addCleanup(delete_embed_test_annex_file, annex_file.name)
		attach_embed_test_annex_file_to_sales_invoice(annex_file, sales_invoice)
		sales_invoice.append(
			"einvoice_attachments",
			{"file": annex_file.name, "file_name": annex_file.file_name},
		)

		broken_url = f"/files/missing-legacy-{frappe.generate_hash(length=8)}.png"
		sales_invoice.einvoice_embedded_document = broken_url
		frappe.clear_messages()

		with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_einvoice"):
			validate_doc(sales_invoice, "validate")

		self.assertEqual(sales_invoice.einvoice_embedded_document, "")
		self.assertEqual(len(sales_invoice.einvoice_attachments), 1)
		self.assertEqual(sales_invoice.einvoice_attachments[0].file, annex_file.name)

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
		self.assertIn("was removed", error_logs[0].error.lower())

		assert_single_orange_message(broken_url)
		warning = next(message for message in frappe.get_message_log() if broken_url in message.message)
		self.assertIn("already has rows", warning.message)

	def test_migrate_legacy_field_treats_cross_invoice_url_as_broken(self):
		set_multi_attachment_embed_enabled(True)
		invoice_a = ensure_embed_test_sales_invoice()
		invoice_b = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, invoice_a.name)
		self.addCleanup(delete_embed_test_sales_invoice, invoice_b.name)

		annex_file_name = f"cross-invoice-legacy-{frappe.generate_hash(length=8)}.png"
		annex_content = LOCAL_ANNEX_PNG_BYTES + frappe.generate_hash(length=8).encode()
		annex_file = create_embed_test_annex_file(file_name=annex_file_name, content=annex_content)
		self.addCleanup(delete_embed_test_annex_file, annex_file.name)
		attach_embed_test_annex_file_to_sales_invoice(annex_file, invoice_b)

		invoice_a.einvoice_embedded_document = annex_file.file_url
		frappe.clear_messages()

		with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_einvoice"):
			validate_doc(invoice_a, "validate")

		self.assertEqual(invoice_a.einvoice_embedded_document, annex_file.file_url)
		self.assertEqual(len(invoice_a.einvoice_attachments), 0)
		assert_single_orange_message(annex_file.file_url)

	def test_validate_doc_blocks_duplicate_embed_filenames(self):
		set_multi_attachment_embed_enabled(True)
		doc = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, doc.name)

		settings = frappe.get_doc("E Invoice Settings")
		previous_action = settings.error_action_on_save
		settings.error_action_on_save = ""
		settings.save(ignore_permissions=True)
		self.addCleanup(
			lambda: frappe.get_doc("E Invoice Settings")
			.update({"error_action_on_save": previous_action})
			.save(ignore_permissions=True)
		)

		append_attachment_rows(
			doc,
			[
				{"file": "F-VALIDATE-1", "file_name": "duplicate.pdf"},
				{"file": "F-VALIDATE-2", "file_name": "duplicate.pdf"},
			],
		)
		frappe.clear_messages()

		with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_einvoice"):
			with self.assertRaises(frappe.ValidationError) as error:
				validate_doc(doc, "validate")

		self.assertEqual(len(doc.einvoice_attachments), 2)
		self.assertIn("unique filename", str(error.exception).lower())

	def test_create_einvoice_embeds_legacy_attachment(self):
		sales_invoice, annex_file = create_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		self.addCleanup(delete_embed_test_annex_file, annex_file.name)

		generator = build_einvoice_generator(sales_invoice)
		generator.create_einvoice()

		refs = generator.doc.trade.agreement.additional_references.children
		self.assertEqual(len(refs), 1)

		ref = refs[0]
		self.assertEqual(str(ref.type_code), "916")
		self.assertEqual(ref.issuer_assigned_id._text, annex_file.name)

		attached_object = ref.attached_object
		self.assertEqual(attached_object._mime_code, "image/png")
		self.assertEqual(attached_object._filename, annex_file.file_name)
		self.assertEqual(attached_object._text, as_base_64(annex_file.get_content(encodings=[])))

	def test_get_table_embed_attachments_returns_rows_in_order(self):
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
		attach_embed_test_annex_file_to_sales_invoice(annex_one, sales_invoice)
		attach_embed_test_annex_file_to_sales_invoice(annex_two, sales_invoice)
		append_attachment_rows(
			sales_invoice,
			[
				{"file": annex_two.name, "file_name": annex_two.file_name},
				{"file": annex_one.name, "file_name": annex_one.file_name},
			],
		)
		sales_invoice.save(ignore_permissions=True)
		sales_invoice.reload()

		self.assertEqual(
			_get_table_embed_attachments(sales_invoice),
			[
				EmbedAttachment(file=annex_two.name, file_name=annex_two.file_name),
				EmbedAttachment(file=annex_one.name, file_name=annex_one.file_name),
			],
		)

	def test_validate_doc_blocks_foreign_embed_file(self):
		set_multi_attachment_embed_enabled(True)
		sales_invoice = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)

		foreign_annex = create_embed_test_annex_file(
			file_name=f"foreign-annex-{frappe.generate_hash(length=8)}.png",
			content=LOCAL_ANNEX_PNG_BYTES + b"foreign",
		)
		other_invoice = ensure_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, other_invoice.name)
		self.addCleanup(delete_embed_test_annex_file, foreign_annex.name)
		attach_embed_test_annex_file_to_sales_invoice(foreign_annex, other_invoice)

		append_attachment_rows(
			sales_invoice, [{"file": foreign_annex.name, "file_name": foreign_annex.file_name}]
		)
		frappe.clear_messages()

		with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_einvoice"):
			with self.assertRaises(frappe.ValidationError) as error:
				validate_doc(sales_invoice, "validate")

		self.assertIn("attached to this document", str(error.exception).lower())

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
		attach_embed_test_annex_file_to_sales_invoice(annex_one, sales_invoice)
		attach_embed_test_annex_file_to_sales_invoice(annex_two, sales_invoice)
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
			self.assertEqual(ref.attached_object._filename, annex_file.file_name)
			self.assertEqual(ref.attached_object._text, as_base_64(resolved_file.get_content(encodings=[])))

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
		attach_embed_test_annex_file_to_sales_invoice(annex_file, sales_invoice)
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
