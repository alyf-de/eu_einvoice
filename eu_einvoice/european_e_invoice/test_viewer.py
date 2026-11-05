# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from pathlib import Path
from unittest.mock import patch

import frappe
from frappe import _
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice import viewer as viewer_module
from eu_einvoice.european_e_invoice.protocol import (
	UPLOAD_MODE_ALL,
	UPLOAD_MODE_NONE,
	create_protocol_stub_from_file,
	store_validation_result,
)
from eu_einvoice.european_e_invoice.viewer import (
	_protocol_for_file,
	_xml_from_document,
	files_with_protocol,
	render_from_file,
)
from eu_einvoice.visualization import EInvoiceFormat

CII_FIXTURE = (
	Path(__file__).resolve().parents[1]
	/ "visualization"
	/ "test_instances"
	/ "included-notes-bg-1-uncefact.xml"
)


class TestEInvoiceViewer(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.db.delete("E Invoice Protocol")
		settings = frappe.get_single("E Invoice Settings")
		settings.create_protocol_on_upload = UPLOAD_MODE_ALL
		settings.flags.ignore_permissions = True
		settings.save()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("E Invoice Protocol")
		settings = frappe.get_single("E Invoice Settings")
		settings.create_protocol_on_upload = UPLOAD_MODE_NONE
		settings.flags.ignore_permissions = True
		settings.save()
		super().tearDown()

	def _make_private_xml_file(self) -> str:
		content = CII_FIXTURE.read_bytes()
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "viewer-test-einvoice.xml",
				"content": content,
				"is_private": 1,
			}
		)
		with patch(
			"eu_einvoice.european_e_invoice.protocol.maybe_create_protocol_stub",
			return_value=None,
		):
			file_doc.insert(ignore_permissions=True)
		return file_doc.name

	def test_files_with_protocol_for_readable_file(self):
		file_name = self._make_private_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		create_protocol_stub_from_file(file_name)
		store_validation_result(
			file_doc=file_doc,
			xml_bytes=CII_FIXTURE.read_bytes(),
			einvoice_format=EInvoiceFormat.CII.value,
			invoice_profile="EN 16931",
			errors=[],
			warnings=[],
		)

		result = files_with_protocol([file_name])
		self.assertEqual(result.get(file_name), "Valid")

		by_url = files_with_protocol([file_doc.file_url])
		self.assertEqual(by_url.get(file_name), "Valid")

	def test_files_with_protocol_hides_unreadable_files(self):
		"""When File get_list returns nothing, no protocol status is leaked."""
		file_name = self._make_private_xml_file()
		create_protocol_stub_from_file(file_name)

		with patch(
			"eu_einvoice.european_e_invoice.viewer.frappe.get_list",
			return_value=[],
		):
			self.assertEqual(files_with_protocol([file_name]), {})

	def test_files_with_protocol_without_protocol_permission(self):
		"""Missing Protocol read must not raise during attachment decoration."""
		file_name = self._make_private_xml_file()
		create_protocol_stub_from_file(file_name)

		with patch(
			"eu_einvoice.european_e_invoice.viewer.frappe.has_permission",
			side_effect=lambda doctype, *args, **kwargs: doctype != "E Invoice Protocol",
		):
			self.assertEqual(files_with_protocol([file_name]), {})

	def test_protocol_for_file_without_protocol_permission(self):
		"""Viewer may omit the protocol link when the caller cannot read Protocol."""
		file_name = self._make_private_xml_file()
		create_protocol_stub_from_file(file_name)

		with patch(
			"eu_einvoice.european_e_invoice.viewer.frappe.has_permission",
			side_effect=lambda doctype, *args, **kwargs: doctype != "E Invoice Protocol",
		):
			self.assertIsNone(_protocol_for_file(file_name))

	def test_files_with_protocol_caps_identifier_list(self):
		"""Huge identifier lists are truncated before File / Protocol lookups."""
		oversized = [f"FILE-{i}" for i in range(viewer_module._MAX_IDENTIFIERS + 50)]
		seen_limits: list[int] = []

		def capture_get_list(*_args, **kwargs):
			seen_limits.append(kwargs.get("limit"))
			return []

		with patch(
			"eu_einvoice.european_e_invoice.viewer.frappe.get_list",
			side_effect=capture_get_list,
		):
			self.assertEqual(files_with_protocol(oversized), {})

		self.assertTrue(seen_limits)
		self.assertTrue(all(limit == viewer_module._MAX_IDENTIFIERS for limit in seen_limits))

	def test_render_from_file_permission_denied(self):
		file_name = self._make_private_xml_file()

		def deny_read(*_args, **_kwargs):
			frappe.throw(_("No"), frappe.PermissionError)

		with patch.object(frappe.model.document.Document, "check_permission", side_effect=deny_read):
			with self.assertRaises(frappe.PermissionError):
				render_from_file(file_name)

	def test_xml_from_document_denies_unreadable_attach_url(self):
		"""Attach URLs must not be read from disk without File permission.

		A draft can keep another private file's URL after a failed attachment
		insert; visualization must not bypass File checks via Path.read_bytes.
		"""
		file_name = self._make_private_xml_file()
		file_url = frappe.db.get_value("File", file_name, "file_url")
		doc = frappe._dict(
			doctype="Purchase Invoice",
			name="PINV-VIEWER-TEST",
			supplier_invoice_file=file_url,
		)

		with patch(
			"eu_einvoice.european_e_invoice.viewer.frappe.get_list",
			return_value=[],
		):
			with patch(
				"eu_einvoice.european_e_invoice.viewer.get_xml_bytes",
			) as mock_get_xml:
				with self.assertRaises(frappe.PermissionError):
					_xml_from_document(doc)
				mock_get_xml.assert_not_called()

	def test_xml_from_document_reads_via_file_permission(self):
		"""Readable Attach URLs are resolved through **File** before reading."""
		file_name = self._make_private_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		doc = frappe._dict(
			doctype="Purchase Invoice",
			name="PINV-VIEWER-TEST",
			supplier_invoice_file=file_doc.file_url,
		)

		xml_bytes = _xml_from_document(doc)
		self.assertEqual(xml_bytes, CII_FIXTURE.read_bytes())

	def test_render_unsupported_format_throws(self):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "plain.xml",
				"content": b'<?xml version="1.0"?><root/>',
				"is_private": 1,
			}
		)
		with patch(
			"eu_einvoice.european_e_invoice.protocol.maybe_create_protocol_stub",
			return_value=None,
		):
			file_doc.insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			render_from_file(file_doc.name)
