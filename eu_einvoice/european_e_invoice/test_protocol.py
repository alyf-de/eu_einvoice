# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import (
	_file_content_as_bytes,
	get_xml_bytes,
)
from eu_einvoice.european_e_invoice.protocol import (
	PROTOCOL_PARENT_DOCTYPES,
	_changed_attach_urls,
	_company_from_reference,
	_effective_cii_profile,
	_flush_import_protocol_payload,
	_read_xml_bytes_quiet,
	_ruleset_hash_for_format,
	maybe_validate_protocol_on_attach,
	maybe_validate_protocol_on_parent_attach,
	store_validation_from_import,
	store_validation_result,
	validate_protocol_for_file,
)
from eu_einvoice.schematron import (
	get_ruleset_hash,
	get_saxon_version,
	get_validation_errors,
	get_validator_version,
)
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

	def tearDown(self):
		frappe.db.delete("E Invoice Protocol")
		super().tearDown()

	def _make_file(
		self,
		*,
		content: bytes,
		file_name: str = "test-einvoice.xml",
		attached_to_doctype: str | None = None,
		attached_to_name: str | None = None,
		is_private: int = 1,
	) -> str:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"content": content,
				"is_private": is_private,
				"attached_to_doctype": attached_to_doctype,
				"attached_to_name": attached_to_name,
			}
		)
		with patch(
			"eu_einvoice.european_e_invoice.protocol.maybe_validate_protocol_on_attach",
			return_value=None,
		):
			file_doc.insert(ignore_permissions=True)
		return file_doc.name

	def _make_xml_file(self, **kwargs) -> str:
		return self._make_file(content=CII_FIXTURE.read_bytes(), **kwargs)

	def test_protocol_parents_are_import_and_purchase_invoice(self):
		self.assertEqual(PROTOCOL_PARENT_DOCTYPES, ("E Invoice Import", "Purchase Invoice"))

	def test_read_xml_bytes_quiet_reads_public_file(self):
		file_name = self._make_xml_file(is_private=0, file_name="public-protocol.xml")
		file_doc = frappe.get_doc("File", file_name)
		self.assertTrue(file_doc.file_url.startswith("/files/"))
		self.assertEqual(_read_xml_bytes_quiet(file_doc), CII_FIXTURE.read_bytes())

	def test_get_xml_bytes_reads_public_file(self):
		file_name = self._make_xml_file(is_private=0, file_name="public-import.xml")
		file_doc = frappe.get_doc("File", file_name)
		self.assertEqual(get_xml_bytes(file_doc.file_url), CII_FIXTURE.read_bytes())

	def test_file_content_as_bytes_preserves_bom_and_declared_charset(self):
		"""Regression: default File.get_content() re-decodes text and rewrites bytes as UTF-8."""
		# UTF-8 BOM must stay so file_hash matches the stored blob.
		with_bom = b"\xef\xbb\xbf<?xml version='1.0' encoding='UTF-8'?><root/>"
		# Latin-1 ä (0xe4); re-encoding as UTF-8 would become c3 a4 and garble the invoice.
		latin1 = b'<?xml version="1.0" encoding="ISO-8859-1"?><root>\xe4</root>'
		for payload, name in ((with_bom, "bom.xml"), (latin1, "latin1.xml")):
			with self.subTest(file_name=name):
				file_name = self._make_file(content=payload, file_name=name, is_private=0)
				file_doc = frappe.get_doc("File", file_name)
				# Force a disk read (clear in-memory content left from insert).
				file_doc.content = None
				self.assertEqual(_file_content_as_bytes(file_doc), payload)

	def test_store_validation_persists_schematron_messages(self):
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
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.error_count, len(errors))
		self.assertEqual(protocol.warning_count, len(warnings))
		self.assertEqual(protocol.message_count, len(errors) + len(warnings))
		self.assertEqual(protocol.result, "Invalid" if errors else "Valid")
		self.assertEqual(protocol.validation_errors or "", "\n".join(errors))
		self.assertEqual(protocol.validation_warnings or "", "\n".join(warnings))
		self.assertIn("EN16931-CII-validation-preprocessed.xsl", protocol.validator_version or "")
		self.assertIn(get_saxon_version().split()[0], protocol.validator_version or "")
		self.assertEqual(protocol.validator_version, get_validator_version(EInvoiceProfile.EN16931))

	def test_each_file_gets_own_protocol(self):
		"""Two Files with the same XML each get their own protocol row (no reuse)."""
		first = self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-A")
		second = self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-B")
		validate_protocol_for_file(first)
		validate_protocol_for_file(second)
		first_protocol = frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")
		second_protocol = frappe.db.get_value("E Invoice Protocol", {"file": second}, "name")
		self.assertTrue(first_protocol)
		self.assertTrue(second_protocol)
		self.assertNotEqual(first_protocol, second_protocol)

	def test_protocol_file_field_is_unique(self):
		file_name = self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-1")
		validate_protocol_for_file(file_name)
		validate_protocol_for_file(file_name)
		self.assertEqual(frappe.db.count("E Invoice Protocol", {"file": file_name}), 1)

	def test_deleting_file_deletes_protocol(self):
		file_name = self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-1")
		validate_protocol_for_file(file_name)
		self.assertTrue(frappe.db.exists("E Invoice Protocol", {"file": file_name}))
		frappe.delete_doc("File", file_name, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": file_name}))

	def test_malformed_xml_creates_no_protocol(self):
		file_name = self._make_file(
			content=b"not-xml",
			file_name="broken.xml",
			attached_to_doctype="Purchase Invoice",
			attached_to_name="PINV-1",
		)
		validate_protocol_for_file(file_name)
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": file_name}))

	def test_non_einvoice_xml_creates_no_protocol(self):
		file_name = self._make_file(
			content=UNSUPPORTED_XML,
			file_name="other.xml",
			attached_to_doctype="Purchase Invoice",
			attached_to_name="PINV-1",
		)
		validate_protocol_for_file(file_name)
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": file_name}))

	def test_ubl_is_unsupported(self):
		file_name = self._make_file(
			content=UBL_FIXTURE.read_bytes(),
			file_name="ubl.xml",
			attached_to_doctype="Purchase Invoice",
			attached_to_name="PINV-UBL",
		)
		validate_protocol_for_file(file_name)
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.result, "Unsupported")
		self.assertEqual(protocol.einvoice_format, EInvoiceFormat.UBL_INVOICE.value)

	def test_doctype_records_invalid_protocol(self):
		"""DOCTYPE XML is detected as CII but Schematron rejects it; persist Invalid + reason."""
		base = CII_FIXTURE.read_bytes()
		decl_end = base.find(b"?>")
		self.assertNotEqual(decl_end, -1)
		content = base[: decl_end + 2] + b"\n<!DOCTYPE CrossIndustryInvoice>\n" + base[decl_end + 2 :]
		file_name = self._make_file(
			content=content,
			file_name="doctype-invoice.xml",
			attached_to_doctype="Purchase Invoice",
			attached_to_name="PINV-DOCTYPE",
		)
		validate_protocol_for_file(file_name)
		protocol_name = frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		self.assertTrue(protocol_name)
		protocol = frappe.get_doc("E Invoice Protocol", protocol_name)
		self.assertEqual(protocol.result, "Invalid")
		self.assertIn("DOCTYPE", protocol.validation_errors or "")
		self.assertEqual(protocol.error_count, 1)

	def test_folder_and_remote_file_hooks_noop(self):
		folder = frappe.get_doc({"doctype": "File", "file_name": "Folder", "is_folder": 1})
		remote = frappe.get_doc(
			{"doctype": "File", "file_name": "remote.xml", "file_url": "https://example.com/a.xml"}
		)
		# is_remote_file is a read-only property derived from file_url
		self.assertTrue(remote.is_remote_file)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(folder)
			maybe_validate_protocol_on_attach(remote)
			enqueue.assert_not_called()

	def test_attach_hook_skips_unlisted_doctype(self):
		file_doc = frappe.get_doc(
			"File",
			self._make_xml_file(attached_to_doctype="Sales Invoice", attached_to_name="SI-TEST"),
		)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_not_called()

	def test_attach_hook_skips_import_einvoice_field(self):
		file_name = self._make_xml_file(attached_to_doctype="E Invoice Import", attached_to_name="EII-1")
		frappe.db.set_value("File", file_name, "attached_to_field", "einvoice", update_modified=False)
		file_doc = frappe.get_doc("File", file_name)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_not_called()

	def test_attach_hook_enqueues_sidebar_file_on_e_invoice_import(self):
		file_name = self._make_xml_file(attached_to_doctype="E Invoice Import", attached_to_name="EII-SIDE")
		file_doc = frappe.get_doc("File", file_name)
		self.assertFalse(file_doc.attached_to_field)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_called_once()
			self.assertIn("validate_protocol_for_file", enqueue.call_args.args[0])

	def test_attach_hook_enqueues_validation_for_purchase_invoice(self):
		file_doc = frappe.get_doc(
			"File",
			self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-1"),
		)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_called_once()
			self.assertIn("validate_protocol_for_file", enqueue.call_args.args[0])
			self.assertTrue(enqueue.call_args.kwargs["deduplicate"])
			self.assertEqual(
				enqueue.call_args.kwargs["job_id"],
				f"einvoice-protocol-validate-{file_doc.name}",
			)

	def test_parent_attach_hook_validates_orphan_owned_via_set_value(self):
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		frappe.db.set_value(
			"File",
			file_name,
			{
				"attached_to_name": "PINV-ORPHAN-1",
				"attached_to_doctype": "Purchase Invoice",
				"attached_to_field": "supplier_invoice_file",
			},
		)
		parent = frappe.new_doc("Purchase Invoice")
		parent.name = "PINV-ORPHAN-1"
		parent.supplier_invoice_file = file_doc.file_url

		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_parent_attach(parent)
			enqueue.assert_called_once()
			self.assertEqual(enqueue.call_args.kwargs.get("file_name"), file_name)

	def test_parent_attach_hook_skips_when_attach_unchanged(self):
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		frappe.db.set_value(
			"File",
			file_name,
			{
				"attached_to_name": "PINV-ORPHAN-2",
				"attached_to_doctype": "Purchase Invoice",
				"attached_to_field": "supplier_invoice_file",
			},
		)
		parent = frappe.new_doc("Purchase Invoice")
		parent.name = "PINV-ORPHAN-2"
		parent.supplier_invoice_file = file_doc.file_url
		parent._doc_before_save = frappe.new_doc("Purchase Invoice")
		parent._doc_before_save.supplier_invoice_file = file_doc.file_url

		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_parent_attach(parent)
			enqueue.assert_not_called()

	def test_unrelated_doctype_parent_hook_is_noop(self):
		"""Regression: no \"*\" hook - Error Log / ToDo must not touch protocol."""
		todo = frappe.new_doc("ToDo")
		todo.description = "no protocol"
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_parent_attach(todo)
			enqueue.assert_not_called()

	def test_changed_attach_urls_includes_child_table_fields(self):
		parent = frappe.new_doc("Purchase Invoice")
		row = parent.append("items", {})
		row.image = "/private/files/child-einvoice.xml"
		self.assertIn("/private/files/child-einvoice.xml", _changed_attach_urls(parent))

	def test_attach_hook_updates_reference_when_already_settled(self):
		file_name = self._make_xml_file()
		validate_protocol_for_file(file_name)
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertIn(protocol.result, ("Valid", "Invalid", "Unsupported"))

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
			with patch(
				"eu_einvoice.european_e_invoice.protocol._company_from_reference",
				return_value="_Test Company",
			):
				maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_not_called()

		protocol.reload()
		self.assertEqual(protocol.reference_doctype, "Purchase Invoice")
		self.assertEqual(protocol.reference_name, "PINV-SETTLED")
		self.assertEqual(protocol.company, "_Test Company")

	def test_company_from_reference_reads_company_field(self):
		self.assertIsNone(_company_from_reference(None, None))
		self.assertIsNone(_company_from_reference("File", "x"))
		company = frappe.db.get_value("Company", {}, "name")
		self.assertTrue(company)
		import_doc = frappe.get_doc({"doctype": "E Invoice Import", "company": company}).insert(
			ignore_permissions=True
		)
		self.addCleanup(lambda: frappe.delete_doc("E Invoice Import", import_doc.name, force=True))
		self.assertEqual(_company_from_reference("E Invoice Import", import_doc.name), company)

	def test_validate_stores_company_from_attached_document(self):
		company = frappe.db.get_value("Company", {}, "name")
		import_doc = frappe.get_doc({"doctype": "E Invoice Import", "company": company}).insert(
			ignore_permissions=True
		)
		self.addCleanup(lambda: frappe.delete_doc("E Invoice Import", import_doc.name, force=True))
		file_name = self._make_xml_file(
			attached_to_doctype="E Invoice Import",
			attached_to_name=import_doc.name,
		)
		validate_protocol_for_file(file_name)
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.company, company)
		self.assertEqual(protocol.reference_doctype, "E Invoice Import")
		self.assertEqual(protocol.reference_name, import_doc.name)

	def _attach_file_to_import(self, file_name: str, import_name: str, fieldname: str = "einvoice"):
		frappe.db.set_value(
			"File",
			file_name,
			{
				"attached_to_doctype": "E Invoice Import",
				"attached_to_name": import_name,
				"attached_to_field": fieldname,
			},
			update_modified=False,
		)

	def test_store_validation_from_import(self):
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		xml_bytes = CII_FIXTURE.read_bytes()
		import_name = "EII-STORE-1"
		self._attach_file_to_import(file_name, import_name)
		import_doc = frappe._dict(
			doctype="E Invoice Import",
			name=import_name,
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
		self.assertEqual(protocol.reference_name, import_name)

	def test_store_validation_from_import_uses_this_imports_file(self):
		xml_bytes = CII_FIXTURE.read_bytes()
		import_name = "EII-URL-COPY"
		import_file_name = self._make_xml_file()
		self._attach_file_to_import(import_file_name, import_name)
		file_url = frappe.db.get_value("File", import_file_name, "file_url")

		other = frappe.get_doc("File", import_file_name).create_attachment_copy(
			"E Invoice Import",
			"EII-OTHER",
			"einvoice",
		)

		import_doc = frappe._dict(
			doctype="E Invoice Import",
			name=import_name,
			einvoice=file_url,
			profile=EInvoiceProfile.EN16931.value,
		)
		store_validation_from_import(import_doc, xml_bytes, ["only-import"], [])
		import_protocol = frappe.get_doc(
			"E Invoice Protocol",
			frappe.db.get_value("E Invoice Protocol", {"file": import_file_name}, "name"),
		)
		self.assertEqual(import_protocol.validation_errors, "only-import")
		self.assertEqual(import_protocol.reference_name, import_name)
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": other.name}))

	def test_import_protocol_store_waits_until_file_attached(self):
		"""Deferred payload is flushed on Import on_update after attach_files."""
		company = frappe.db.get_value("Company", {}, "name")
		import_doc = frappe.get_doc({"doctype": "E Invoice Import", "company": company}).insert(
			ignore_permissions=True
		)
		self.addCleanup(lambda: frappe.delete_doc("E Invoice Import", import_doc.name, force=True))

		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		import_doc.einvoice = file_doc.file_url
		import_doc.profile = EInvoiceProfile.EN16931.value
		import_doc.flags.einvoice_protocol_payload = {
			"xml_bytes": CII_FIXTURE.read_bytes(),
			"errors": ["deferred"],
			"warnings": [],
		}
		# Simulate attach_files linking the orphan before flush
		self._attach_file_to_import(file_name, import_doc.name)
		_flush_import_protocol_payload(import_doc)

		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.validation_errors, "deferred")
		self.assertEqual(protocol.file, file_name)
		self.assertFalse(import_doc.flags.einvoice_protocol_payload)

	def test_ruleset_hash_uses_en16931_fallback_for_unknown_profile(self):
		self.assertEqual(
			_ruleset_hash_for_format(EInvoiceFormat.CII, "unknown-profile"),
			get_ruleset_hash(EInvoiceProfile.EN16931),
		)
		self.assertEqual(_effective_cii_profile("unknown-profile"), EInvoiceProfile.EN16931)
		self.assertEqual(_ruleset_hash_for_format(EInvoiceFormat.UBL_INVOICE, None), "")

	def test_import_xrechnung_runs_en16931_as_well(self):
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		xml_bytes = CII_FIXTURE.read_bytes()
		import_name = "EII-XR-1"
		self._attach_file_to_import(file_name, import_name)

		with patch(
			"eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.get_validation_errors",
		) as mock_validate:
			mock_validate.side_effect = [
				(["xr-err"], ["xr-warn"]),
				(["en-err"], ["en-warn"]),
			]
			# Call through store path with XRechnung profile after dual validation elsewhere
			store_validation_from_import(
				frappe._dict(
					doctype="E Invoice Import",
					name=import_name,
					einvoice=file_doc.file_url,
					profile=EInvoiceProfile.XRECHNUNG.value,
				),
				xml_bytes,
				["xr-err", "en-err"],
				["xr-warn", "en-warn"],
			)

		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertIn("xr-err", protocol.validation_errors)
		self.assertIn("en-err", protocol.validation_errors)
