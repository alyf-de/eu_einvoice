# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import EInvoiceImport
from eu_einvoice.european_e_invoice.protocol import (
	UPLOAD_MODE_ALL,
	UPLOAD_MODE_NONE,
	UPLOAD_MODE_SELECTED,
	create_protocol_stub_from_file,
	maybe_create_protocol_stub,
	maybe_validate_protocol_on_attach,
	reuse_validation_for_hashes,
	store_validation_from_import,
	store_validation_result,
	validate_protocol_for_file,
	xml_content_hash,
)
from eu_einvoice.schematron import get_saxon_version, get_validation_errors, get_validator_version
from eu_einvoice.utils import EInvoiceProfile
from eu_einvoice.visualization import EInvoiceFormat

FIXTURES = Path(__file__).resolve().parents[1] / "visualization" / "test_instances"
CII_FIXTURE = FIXTURES / "included-notes-bg-1-uncefact.xml"
UBL_FIXTURE = FIXTURES / "maxRechnung_ubl.xml"

UNSUPPORTED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<NotAnInvoice><ID>1</ID></NotAnInvoice>
"""


class TestEInvoiceProtocol(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.delete("E Invoice Protocol")
		settings = frappe.get_single("E Invoice Settings")
		settings.create_protocol_on_upload = UPLOAD_MODE_ALL
		settings.protocol_doctypes = []
		settings.append("protocol_doctypes", {"document_type": "E Invoice Import"})
		settings.append("protocol_doctypes", {"document_type": "Purchase Invoice"})
		settings.flags.ignore_permissions = True
		settings.save()

	def tearDown(self):
		frappe.db.delete("E Invoice Protocol")
		settings = frappe.get_single("E Invoice Settings")
		settings.create_protocol_on_upload = UPLOAD_MODE_NONE
		settings.flags.ignore_permissions = True
		settings.save()
		super().tearDown()

	def _make_file(
		self,
		*,
		content: bytes,
		file_name: str = "test-einvoice.xml",
		attached_to_doctype: str | None = None,
		attached_to_name: str | None = None,
	) -> str:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"content": content,
				"is_private": 1,
				"attached_to_doctype": attached_to_doctype,
				"attached_to_name": attached_to_name,
			}
		)
		with patch(
			"eu_einvoice.european_e_invoice.protocol.maybe_create_protocol_stub",
			return_value=None,
		):
			with patch(
				"eu_einvoice.european_e_invoice.protocol.maybe_validate_protocol_on_attach",
				return_value=None,
			):
				file_doc.insert(ignore_permissions=True)
		return file_doc.name

	def _make_xml_file(self, **kwargs) -> str:
		return self._make_file(content=CII_FIXTURE.read_bytes(), **kwargs)

	def _set_upload_mode(self, mode: str):
		settings = frappe.get_single("E Invoice Settings")
		settings.create_protocol_on_upload = mode
		settings.flags.ignore_permissions = True
		settings.save()

	def test_stub_creation_stores_hash_and_format(self):
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		create_protocol_stub_from_file(file_name)
		protocol_name = frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		self.assertTrue(protocol_name)
		protocol = frappe.get_doc("E Invoice Protocol", protocol_name)
		self.assertEqual(protocol.result, "Pending")
		self.assertEqual(protocol.einvoice_format, EInvoiceFormat.CII.value)
		self.assertTrue(protocol.file_hash)
		self.assertEqual(protocol.content_hash, file_doc.content_hash)
		self.assertEqual(protocol.file_name, "test-einvoice.xml")
		self.assertFalse(protocol.copied_from)

	def test_store_validation_reuses_schematron_messages(self):
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		xml_bytes = CII_FIXTURE.read_bytes()
		errors, warnings = get_validation_errors(xml_bytes.decode("utf-8"), EInvoiceProfile.EN16931)
		store_validation_result(
			file_doc=file_doc,
			xml_bytes=xml_bytes,
			einvoice_format=EInvoiceFormat.CII.value,
			invoice_profile=EInvoiceProfile.EN16931.value,
			errors=errors,
			warnings=warnings,
			reference_doctype="Purchase Invoice",
			reference_name="PINV-TEST",
		)
		protocol_name = frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		protocol = frappe.get_doc("E Invoice Protocol", protocol_name)
		self.assertEqual(protocol.error_count, len(errors))
		self.assertEqual(protocol.warning_count, len(warnings))
		self.assertEqual(protocol.message_count, len(errors) + len(warnings))
		self.assertEqual(protocol.result, "Invalid" if errors else "Valid")
		self.assertEqual(protocol.validation_errors or "", "\n".join(errors))
		self.assertEqual(protocol.validation_warnings or "", "\n".join(warnings))
		self.assertIn("EN 16931", protocol.validator_version or "")
		self.assertIn(get_saxon_version().split()[0], protocol.validator_version or "")
		self.assertEqual(protocol.validator_version, get_validator_version(EInvoiceProfile.EN16931))
		self.assertFalse(protocol.copied_from)

	def test_each_file_gets_own_protocol(self):
		"""Second File with the same XML gets its own protocol row (1:1 with File)."""
		first = self._make_xml_file()
		create_protocol_stub_from_file(first)
		first_protocol = frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")
		self.assertTrue(first_protocol)

		second = self._make_xml_file()
		create_protocol_stub_from_file(second)
		second_protocol = frappe.db.get_value("E Invoice Protocol", {"file": second}, "name")
		self.assertTrue(second_protocol)
		self.assertNotEqual(first_protocol, second_protocol)
		self.assertEqual(
			frappe.db.count("E Invoice Protocol", {"content_hash": frappe.get_doc("File", first).content_hash}),
			2,
		)

	def test_upload_hook_skips_when_disabled(self):
		self._set_upload_mode(UPLOAD_MODE_NONE)
		file_doc = frappe.get_doc("File", self._make_xml_file())
		maybe_create_protocol_stub(file_doc)
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": file_doc.name}))

	def test_malformed_xml_stub_does_not_raise(self):
		"""Garbage .xml must not break File hooks or stub creation."""
		file_name = self._make_file(content=b"not <?xml at all <<<", file_name="garbage.xml")
		self.assertIsNone(create_protocol_stub_from_file(file_name))
		maybe_create_protocol_stub(frappe.get_doc("File", file_name))
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": file_name}))

	def test_upload_hook_enqueues_stub_creation(self):
		file_doc = frappe.get_doc("File", self._make_xml_file())
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_create_protocol_stub(file_doc)
			enqueue.assert_called_once()
			self.assertEqual(enqueue.call_args.kwargs["file_name"], file_doc.name)
			self.assertIn("create_protocol_stub_from_file", enqueue.call_args.args[0])

	def test_deleting_file_keeps_protocol(self):
		"""Protocol is in ignore_links_on_delete - File delete must not require Protocol delete."""
		file_name = self._make_xml_file()
		create_protocol_stub_from_file(file_name)
		protocol_name = frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		self.assertTrue(protocol_name)

		# force=False: must rely on ignore_links_on_delete, not bypass
		frappe.delete_doc("File", file_name, force=False, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("File", file_name))
		self.assertTrue(frappe.db.exists("E Invoice Protocol", protocol_name))
		protocol = frappe.get_doc("E Invoice Protocol", protocol_name)
		self.assertTrue(protocol.file_hash)
		self.assertEqual(protocol.file_name, "test-einvoice.xml")

	def test_validation_copied_for_same_hashes(self):
		"""Second File with the same content_hash + file_hash copies validation, no Schematron."""
		first = self._make_xml_file()
		create_protocol_stub_from_file(first)
		validate_protocol_for_file(first)
		original = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")
		)
		self.assertIn(original.result, ("Valid", "Invalid"))
		self.assertFalse(original.copied_from)

		second = self._make_xml_file()
		self.assertEqual(
			frappe.get_doc("File", second).content_hash,
			original.content_hash,
		)

		with patch(
			"eu_einvoice.european_e_invoice.protocol.get_validation_errors",
			side_effect=AssertionError("Schematron must not run again for same hashes"),
		) as mocked:
			create_protocol_stub_from_file(second)
			mocked.assert_not_called()

		copy = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": second}, "name")
		)
		self.assertNotEqual(copy.name, original.name)
		self.assertEqual(copy.file, second)
		self.assertEqual(copy.copied_from, original.name)
		self.assertEqual(copy.result, original.result)
		self.assertEqual(copy.validation_errors or "", original.validation_errors or "")
		self.assertEqual(copy.error_count, original.error_count)
		self.assertEqual(copy.content_hash, original.content_hash)
		self.assertEqual(copy.file_hash, original.file_hash)

		with patch(
			"eu_einvoice.european_e_invoice.protocol.get_validation_errors",
			side_effect=AssertionError("Schematron must not run again for same hashes"),
		) as mocked:
			validate_protocol_for_file(second)
			mocked.assert_not_called()

	def test_validation_propagates_to_earlier_pending_stub(self):
		"""Upload stub stays Pending until a later File with the same hashes is validated."""
		first = self._make_xml_file()
		create_protocol_stub_from_file(first)
		pending = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")
		)
		self.assertEqual(pending.result, "Pending")

		second = self._make_xml_file()
		create_protocol_stub_from_file(second)
		validate_protocol_for_file(second)

		validated = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": second}, "name")
		)
		self.assertIn(validated.result, ("Valid", "Invalid"))
		self.assertFalse(validated.copied_from)

		pending.reload()
		self.assertEqual(pending.result, validated.result)
		self.assertEqual(pending.copied_from, validated.name)
		self.assertEqual(pending.validation_errors or "", validated.validation_errors or "")
		self.assertEqual(pending.error_count, validated.error_count)

	def test_selected_mode_skips_unlisted_doctype(self):
		self._set_upload_mode(UPLOAD_MODE_SELECTED)
		file_doc = frappe.get_doc(
			"File",
			self._make_xml_file(attached_to_doctype="Sales Invoice", attached_to_name="SI-TEST"),
		)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_create_protocol_stub(file_doc)
			enqueue.assert_not_called()

	def test_selected_mode_enqueues_for_listed_doctype(self):
		self._set_upload_mode(UPLOAD_MODE_SELECTED)
		file_doc = frappe.get_doc(
			"File",
			self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-TEST"),
		)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_create_protocol_stub(file_doc)
			enqueue.assert_called_once()
			self.assertIn("create_protocol_stub_from_file", enqueue.call_args.args[0])

	def test_ubl_stub_is_unsupported(self):
		file_name = self._make_file(content=UBL_FIXTURE.read_bytes(), file_name="ubl.xml")
		create_protocol_stub_from_file(file_name)
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.result, "Unsupported")
		self.assertEqual(protocol.einvoice_format, EInvoiceFormat.UBL_INVOICE.value)
		self.assertFalse(protocol.copied_from)

	def test_non_einvoice_xml_creates_no_protocol(self):
		file_name = self._make_file(content=UNSUPPORTED_XML, file_name="other.xml")
		self.assertIsNone(create_protocol_stub_from_file(file_name))
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": file_name}))

	def _as_pdf_named_file(self, file_name: str, display_name: str = "invoice.pdf") -> str:
		"""Rename an inserted File to *.pdf without PDF content checks (no real PDF fixture)."""
		frappe.db.set_value("File", file_name, "file_name", display_name)
		return file_name

	def test_pdf_without_embedded_xml_creates_no_protocol(self):
		file_name = self._as_pdf_named_file(self._make_xml_file(), "plain.pdf")
		with patch(
			"eu_einvoice.european_e_invoice.protocol._read_xml_bytes_quiet",
			return_value=None,
		):
			self.assertIsNone(create_protocol_stub_from_file(file_name))
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": file_name}))

	def test_pdf_stub_uses_extracted_xml(self):
		file_name = self._as_pdf_named_file(self._make_xml_file(), "zugferd.pdf")
		xml_bytes = CII_FIXTURE.read_bytes()
		with patch(
			"eu_einvoice.european_e_invoice.protocol._read_xml_bytes_quiet",
			return_value=xml_bytes,
		):
			create_protocol_stub_from_file(file_name)
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.result, "Pending")
		self.assertEqual(protocol.einvoice_format, EInvoiceFormat.CII.value)
		self.assertEqual(protocol.file_hash, xml_content_hash(xml_bytes))
		self.assertEqual(protocol.file_name, "zugferd.pdf")

	def test_pdf_upload_hook_enqueues_stub(self):
		file_doc = frappe.get_doc("File", self._as_pdf_named_file(self._make_xml_file()))
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_create_protocol_stub(file_doc)
			enqueue.assert_called_once()
			self.assertIn("create_protocol_stub_from_file", enqueue.call_args.args[0])

	def test_folder_and_remote_file_hooks_noop(self):
		folder = MagicMock(is_folder=1, is_remote_file=0, name="Folder", file_name="Folder")
		remote = MagicMock(is_folder=0, is_remote_file=1, name="Remote", file_name="r.xml")
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_create_protocol_stub(folder)
			maybe_create_protocol_stub(remote)
			maybe_validate_protocol_on_attach(folder)
			maybe_validate_protocol_on_attach(remote)
			enqueue.assert_not_called()

	def test_attach_hook_skips_e_invoice_import(self):
		file_doc = frappe.get_doc(
			"File",
			self._make_xml_file(attached_to_doctype="E Invoice Import", attached_to_name="EII-1"),
		)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_not_called()

	def test_attach_hook_enqueues_validation_for_purchase_invoice(self):
		file_doc = frappe.get_doc(
			"File",
			self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-1"),
		)
		create_protocol_stub_from_file(file_doc.name)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_called_once()
			self.assertIn("validate_protocol_for_file", enqueue.call_args.args[0])

	def test_attach_hook_updates_reference_when_already_settled(self):
		file_name = self._make_xml_file()
		create_protocol_stub_from_file(file_name)
		validate_protocol_for_file(file_name)
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertIn(protocol.result, ("Valid", "Invalid"))

		file_doc = frappe.get_doc("File", file_name)
		file_doc.db_set(
			{
				"attached_to_doctype": "Purchase Invoice",
				"attached_to_name": "PINV-SETTLED",
			},
			update_modified=False,
		)
		file_doc.reload()

		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_not_called()

		protocol.reload()
		self.assertEqual(protocol.reference_doctype, "Purchase Invoice")
		self.assertEqual(protocol.reference_name, "PINV-SETTLED")

	def test_attach_hook_skips_when_protocol_disabled(self):
		self._set_upload_mode(UPLOAD_MODE_NONE)
		file_doc = frappe.get_doc(
			"File",
			self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-1"),
		)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_not_called()

	def test_store_validation_from_import(self):
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		xml_bytes = CII_FIXTURE.read_bytes()
		import_doc = frappe._dict(
			doctype="E Invoice Import",
			name="EII-STORE-1",
			einvoice=file_doc.file_url,
			profile=EInvoiceProfile.EN16931.value,
		)
		store_validation_from_import(import_doc, xml_bytes, ["BR-1: error"], ["BR-2: warn"])
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.result, "Invalid")
		self.assertEqual(protocol.validation_errors, "BR-1: error")
		self.assertEqual(protocol.validation_warnings, "BR-2: warn")
		self.assertEqual(protocol.reference_doctype, "E Invoice Import")
		self.assertEqual(protocol.reference_name, "EII-STORE-1")
		self.assertEqual(protocol.invoice_profile, EInvoiceProfile.EN16931.value)

	def test_store_validation_from_import_skips_when_disabled(self):
		self._set_upload_mode(UPLOAD_MODE_NONE)
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		import_doc = frappe._dict(
			doctype="E Invoice Import",
			name="EII-SKIP",
			einvoice=file_doc.file_url,
			profile=EInvoiceProfile.EN16931.value,
		)
		store_validation_from_import(import_doc, CII_FIXTURE.read_bytes(), ["err"], [])
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": file_name}))

	def test_import_reuses_prior_protocol_validation(self):
		first = self._make_xml_file()
		create_protocol_stub_from_file(first)
		validate_protocol_for_file(first)
		original = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")
		)

		second = self._make_xml_file()
		second_doc = frappe.get_doc("File", second)
		import_doc = frappe.new_doc("E Invoice Import")
		import_doc.profile = EInvoiceProfile.EN16931.value
		import_doc.einvoice = second_doc.file_url

		with patch(
			"eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.get_validation_errors",
			side_effect=AssertionError("Schematron must not run when hashes match"),
		):
			import_doc._validate_schematron(CII_FIXTURE.read_bytes())

		self.assertEqual(
			(import_doc.validation_errors or "").strip(),
			(original.validation_errors or "").strip(),
		)
		reused = reuse_validation_for_hashes(second_doc.content_hash, xml_content_hash(CII_FIXTURE.read_bytes()))
		self.assertIsNotNone(reused)
		self.assertEqual(reused[2], original.result)

	def test_import_xrechnung_runs_en16931_as_well(self):
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		import_doc = frappe.new_doc("E Invoice Import")
		import_doc.name = "EII-XR-1"
		import_doc.profile = EInvoiceProfile.XRECHNUNG.value
		import_doc.einvoice = file_doc.file_url

		calls: list[EInvoiceProfile] = []

		def fake_validation(_xml: str, profile: EInvoiceProfile):
			calls.append(profile)
			return (["xr-err"] if profile == EInvoiceProfile.XRECHNUNG else ["en-err"]), []

		with patch(
			"eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.get_validation_errors",
			side_effect=fake_validation,
		):
			import_doc._validate_schematron(CII_FIXTURE.read_bytes())

		self.assertEqual(calls, [EInvoiceProfile.XRECHNUNG, EInvoiceProfile.EN16931])
		self.assertIn("xr-err", import_doc.validation_errors)
		self.assertIn("en-err", import_doc.validation_errors)

		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.result, "Invalid")
		self.assertIn("xr-err", protocol.validation_errors)
		self.assertIn("en-err", protocol.validation_errors)
		self.assertEqual(protocol.invoice_profile, EInvoiceProfile.XRECHNUNG.value)
