# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import (
	EInvoiceImport,
	get_xml_bytes,
)
from eu_einvoice.european_e_invoice.protocol import (
	UPLOAD_MODE_ALL,
	UPLOAD_MODE_NONE,
	UPLOAD_MODE_SELECTED,
	_company_from_reference,
	_effective_cii_profile,
	_flush_import_protocol_payload,
	_get_protocol_settings,
	_read_xml_bytes_quiet,
	_ruleset_hash_for_format,
	create_protocol_stub_from_file,
	maybe_create_protocol_stub,
	maybe_validate_protocol_on_attach,
	maybe_validate_protocol_on_parent_attach,
	reuse_validation_for_hashes,
	store_validation_from_import,
	store_validation_result,
	validate_protocol_for_file,
	xml_content_hash,
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

	def test_read_xml_bytes_quiet_reads_public_file(self):
		"""Public `/files/...` must use **File** ``get_content`` (not ``site/files``)."""
		file_name = self._make_xml_file(is_private=0, file_name="public-protocol.xml")
		file_doc = frappe.get_doc("File", file_name)
		self.assertTrue(file_doc.file_url.startswith("/files/"))
		xml_bytes = _read_xml_bytes_quiet(file_doc)
		self.assertEqual(xml_bytes, CII_FIXTURE.read_bytes())

	def test_get_xml_bytes_reads_public_file(self):
		"""Viewer / Import helper must also read public files via **File** content API."""
		file_name = self._make_xml_file(is_private=0, file_name="public-import.xml")
		file_doc = frappe.get_doc("File", file_name)
		self.assertTrue(file_doc.file_url.startswith("/files/"))
		self.assertEqual(get_xml_bytes(file_doc.file_url), CII_FIXTURE.read_bytes())

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
			frappe.db.count(
				"E Invoice Protocol", {"content_hash": frappe.get_doc("File", first).content_hash}
			),
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
			self.assertTrue(enqueue.call_args.kwargs["deduplicate"])
			self.assertEqual(
				enqueue.call_args.kwargs["job_id"],
				f"einvoice-protocol-stub-{file_doc.name}",
			)

	def test_stub_creation_is_idempotent_for_same_file(self):
		"""Concurrent stub + validate jobs must not create a second protocol for one File."""
		file_name = self._make_xml_file()
		first = create_protocol_stub_from_file(file_name)
		second = create_protocol_stub_from_file(file_name)
		self.assertEqual(first.name, second.name)
		self.assertEqual(frappe.db.count("E Invoice Protocol", {"file": file_name}), 1)

	def test_pending_stub_can_enqueue_manual_validation(self):
		file_name = self._make_xml_file()
		protocol = create_protocol_stub_from_file(file_name)
		self.assertEqual(protocol.result, "Pending")

		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			result = protocol.enqueue_validation()
			enqueue.assert_called_once()
			self.assertIn("validate_protocol_for_file", enqueue.call_args.args[0])
			self.assertEqual(enqueue.call_args.kwargs.get("file_name"), file_name)
			self.assertEqual(result["file"], file_name)

		validate_protocol_for_file(file_name)
		protocol.reload()
		self.assertIn(protocol.result, ("Valid", "Invalid"))
		with self.assertRaises(frappe.ValidationError):
			protocol.enqueue_validation()

	def test_protocol_file_field_is_unique(self):
		file_name = self._make_xml_file()
		create_protocol_stub_from_file(file_name)
		dup = frappe.new_doc("E Invoice Protocol")
		dup.result = "Pending"
		dup.file = file_name
		dup.flags.ignore_links = True
		with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
			dup.insert(ignore_permissions=True)

	def test_deleting_file_deletes_protocol(self):
		"""File.on_trash removes the linked protocol (1:1 with File)."""
		file_name = self._make_xml_file()
		create_protocol_stub_from_file(file_name)
		protocol_name = frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		self.assertTrue(protocol_name)

		frappe.delete_doc("File", file_name, force=False, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("File", file_name))
		self.assertFalse(frappe.db.exists("E Invoice Protocol", protocol_name))

	def test_deleting_original_file_clears_copied_from_on_copies(self):
		"""File trash must clear copied_from so the original protocol can be removed."""
		first = self._make_xml_file()
		create_protocol_stub_from_file(first)
		validate_protocol_for_file(first)
		original_name = frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")

		second = self._make_xml_file()
		create_protocol_stub_from_file(second)
		copy = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": second}, "name")
		)
		self.assertEqual(copy.copied_from, original_name)

		frappe.delete_doc("File", first, force=False, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("E Invoice Protocol", original_name))
		copy.reload()
		self.assertFalse(copy.copied_from)

	def test_cannot_delete_original_while_copies_exist(self):
		"""ignore_links_on_delete would allow this; on_trash must still block dangling copied_from."""
		first = self._make_xml_file()
		create_protocol_stub_from_file(first)
		validate_protocol_for_file(first)
		original_name = frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")

		second = self._make_xml_file()
		create_protocol_stub_from_file(second)
		copy = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": second}, "name")
		)
		self.assertEqual(copy.copied_from, original_name)

		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("E Invoice Protocol", original_name, force=False, ignore_permissions=True)
		self.assertTrue(frappe.db.exists("E Invoice Protocol", original_name))

		frappe.delete_doc("E Invoice Protocol", copy.name, force=False, ignore_permissions=True)
		frappe.delete_doc("E Invoice Protocol", original_name, force=False, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("E Invoice Protocol", original_name))

	def test_validation_copied_for_same_hashes(self):
		"""Second File with the same content_hash + file_hash + ruleset_hash copies validation."""
		first = self._make_xml_file()
		create_protocol_stub_from_file(first)
		validate_protocol_for_file(first)
		original = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")
		)
		self.assertIn(original.result, ("Valid", "Invalid"))
		self.assertFalse(original.copied_from)
		self.assertEqual(original.ruleset_hash, get_ruleset_hash(original.invoice_profile))
		self.assertTrue(original.ruleset_hash)

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
		self.assertEqual(copy.ruleset_hash, original.ruleset_hash)

		with patch(
			"eu_einvoice.european_e_invoice.protocol.get_validation_errors",
			side_effect=AssertionError("Schematron must not run again for same hashes"),
		) as mocked:
			validate_protocol_for_file(second)
			mocked.assert_not_called()

	def test_ruleset_change_does_not_reuse_old_result(self):
		"""Identical XML with a different ruleset_hash must re-run Schematron; old row stays."""
		first = self._make_xml_file()
		create_protocol_stub_from_file(first)
		validate_protocol_for_file(first)
		original = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")
		)
		self.assertTrue(original.ruleset_hash)
		old_result = original.result

		# Simulate a bundled ruleset update without deleting history
		frappe.db.set_value("E Invoice Protocol", original.name, "ruleset_hash", "stale-ruleset")

		second = self._make_xml_file()
		with patch(
			"eu_einvoice.european_e_invoice.protocol.get_validation_errors",
			return_value=(["new-rules-error"], []),
		) as mocked:
			create_protocol_stub_from_file(second)
			# Stub stays Pending (no matching ruleset); Schematron not run on stub create
			mocked.assert_not_called()
			validate_protocol_for_file(second)
			mocked.assert_called()

		fresh = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": second}, "name")
		)
		self.assertNotEqual(fresh.name, original.name)
		self.assertFalse(fresh.copied_from)
		self.assertEqual(fresh.ruleset_hash, get_ruleset_hash(fresh.invoice_profile))
		self.assertNotEqual(fresh.ruleset_hash, "stale-ruleset")
		self.assertEqual(fresh.result, "Invalid")
		self.assertIn("new-rules-error", fresh.validation_errors or "")

		original.reload()
		self.assertEqual(original.ruleset_hash, "stale-ruleset")
		self.assertEqual(original.result, old_result)
		self.assertFalse(original.copied_from)

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
			# Attached upload: one validate job (stub + Schematron), not a separate stub job
			self.assertIn("validate_protocol_for_file", enqueue.call_args.args[0])
			self.assertEqual(
				enqueue.call_args.kwargs["job_id"],
				f"einvoice-protocol-validate-{file_doc.name}",
			)

	def test_attached_upload_shares_validate_job_with_on_update(self):
		"""after_insert + on_update must enqueue the same deduped job for attached Files."""
		file_doc = frappe.get_doc(
			"File",
			self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-COMBINED"),
		)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_create_protocol_stub(file_doc)
			maybe_validate_protocol_on_attach(file_doc)
			self.assertEqual(enqueue.call_count, 2)
			methods = [call.args[0] for call in enqueue.call_args_list]
			job_ids = [call.kwargs["job_id"] for call in enqueue.call_args_list]
			self.assertTrue(all("validate_protocol_for_file" in m for m in methods))
			self.assertEqual(job_ids[0], job_ids[1])
			self.assertEqual(job_ids[0], f"einvoice-protocol-validate-{file_doc.name}")

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

	def test_attach_hook_skips_import_einvoice_field(self):
		file_name = self._make_xml_file(attached_to_doctype="E Invoice Import", attached_to_name="EII-1")
		frappe.db.set_value("File", file_name, "attached_to_field", "einvoice", update_modified=False)
		file_doc = frappe.get_doc("File", file_name)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_not_called()

	def test_attach_hook_enqueues_sidebar_file_on_e_invoice_import(self):
		"""Sidebar CII on **E Invoice Import** is not filled by `_validate_schematron`."""
		file_name = self._make_xml_file(attached_to_doctype="E Invoice Import", attached_to_name="EII-SIDE")
		file_doc = frappe.get_doc("File", file_name)
		self.assertFalse(file_doc.attached_to_field)
		create_protocol_stub_from_file(file_name)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_called_once()
			self.assertIn("validate_protocol_for_file", enqueue.call_args.args[0])

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
			self.assertTrue(enqueue.call_args.kwargs["deduplicate"])
			self.assertEqual(
				enqueue.call_args.kwargs["job_id"],
				f"einvoice-protocol-validate-{file_doc.name}",
			)

	def test_parent_attach_hook_validates_orphan_owned_via_set_value(self):
		"""File Manager orphan attached through an Attach field skips File.on_update.

		Frappe uses frappe.db.set_value in attach_files_to_document; parent on_update
		must still enqueue Schematron for the Pending stub.
		"""
		file_name = self._make_xml_file()
		create_protocol_stub_from_file(file_name)
		file_doc = frappe.get_doc("File", file_name)
		self.assertFalse(file_doc.attached_to_doctype)

		# Same path as frappe.core.doctype.file.utils._attach_field_values
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
		# No _doc_before_save => has_value_changed is True for set fields

		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_parent_attach(parent)
			enqueue.assert_called_once()
			self.assertIn("validate_protocol_for_file", enqueue.call_args.args[0])
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

	def test_error_log_does_not_recurse_when_settings_fail(self):
		"""During migrate, settings can fail to load. Logging that error must not run the hook again."""
		with patch(
			"eu_einvoice.european_e_invoice.protocol._get_protocol_settings",
			side_effect=frappe.db.ProgrammingError,
		) as get_settings:
			frappe.log_error(title="E Invoice protocol recursion test")

		get_settings.assert_not_called()

	def test_protocol_disabled_during_migrate(self):
		with patch.dict(frappe.flags, {"in_migrate": True}):
			self.assertEqual(_get_protocol_settings()["create_protocol_on_upload"], UPLOAD_MODE_NONE)

	def test_changed_attach_urls_includes_child_table_fields(self):
		"""Child-table Attach changes must be seen like Frappe's attach_files_to_document."""
		from eu_einvoice.european_e_invoice.protocol import _changed_attach_urls

		parent = frappe.new_doc("Purchase Invoice")
		row = parent.append("items", {})
		row.image = "/private/files/child-einvoice.xml"
		# No _doc_before_save => has_value_changed is True for set fields

		self.assertIn("/private/files/child-einvoice.xml", _changed_attach_urls(parent))

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

		import_doc = frappe.get_doc(
			{
				"doctype": "E Invoice Import",
				"company": company,
			}
		)
		import_doc.insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("E Invoice Import", import_doc.name, force=True))

		self.assertEqual(_company_from_reference("E Invoice Import", import_doc.name), company)

	def test_stub_stores_company_from_attached_document(self):
		company = frappe.db.get_value("Company", {}, "name")
		self.assertTrue(company)

		import_doc = frappe.get_doc(
			{
				"doctype": "E Invoice Import",
				"company": company,
			}
		)
		import_doc.insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("E Invoice Import", import_doc.name, force=True))

		file_name = self._make_xml_file(
			attached_to_doctype="E Invoice Import",
			attached_to_name=import_doc.name,
		)
		create_protocol_stub_from_file(file_name)
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.company, company)
		self.assertEqual(protocol.reference_doctype, "E Invoice Import")
		self.assertEqual(protocol.reference_name, import_doc.name)

	def test_attach_hook_skips_when_protocol_disabled(self):
		self._set_upload_mode(UPLOAD_MODE_NONE)
		file_doc = frappe.get_doc(
			"File",
			self._make_xml_file(attached_to_doctype="Purchase Invoice", attached_to_name="PINV-1"),
		)
		with patch("eu_einvoice.european_e_invoice.protocol.frappe.enqueue") as enqueue:
			maybe_validate_protocol_on_attach(file_doc)
			enqueue.assert_not_called()

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
		self.assertEqual(protocol.invoice_profile, EInvoiceProfile.EN16931.value)

	def test_store_validation_from_import_uses_this_imports_file(self):
		"""Same file_url on two Files: update the import's attachment, not the other File's protocol."""
		xml_bytes = CII_FIXTURE.read_bytes()
		import_name = "EII-URL-COPY"
		import_file_name = self._make_xml_file()
		self._attach_file_to_import(import_file_name, import_name)
		file_url = frappe.db.get_value("File", import_file_name, "file_url")
		create_protocol_stub_from_file(import_file_name)
		import_protocol_name = frappe.db.get_value("E Invoice Protocol", {"file": import_file_name}, "name")

		# Newer File row reusing the same URL (create_attachment_copy). URL-only lookup
		# often returns this row and would rewrite the wrong protocol without the fix.
		other = frappe.get_doc("File", import_file_name).create_attachment_copy(
			"E Invoice Import",
			"EII-OTHER-DOC",
			attached_to_field="einvoice",
			ignore_permissions=True,
		)
		self.assertEqual(other.file_url, file_url)
		self.assertNotEqual(other.name, import_file_name)
		create_protocol_stub_from_file(other.name)
		self.assertGreaterEqual(frappe.db.count("File", {"file_url": file_url}), 2)

		import_doc = frappe._dict(
			doctype="E Invoice Import",
			name=import_name,
			einvoice=file_url,
			profile=EInvoiceProfile.EN16931.value,
		)
		store_validation_from_import(import_doc, xml_bytes, ["BR-copy: error"], [])

		import_protocol = frappe.get_doc("E Invoice Protocol", import_protocol_name)
		self.assertEqual(import_protocol.result, "Invalid")
		self.assertEqual(import_protocol.validation_errors, "BR-copy: error")
		self.assertEqual(import_protocol.reference_doctype, "E Invoice Import")
		self.assertEqual(import_protocol.reference_name, import_name)
		self.assertEqual(import_protocol.file, import_file_name)

		# Sibling hash propagation may settle the other row; must not rewrite its File / reference
		other_protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": other.name}, "name")
		)
		self.assertEqual(other_protocol.file, other.name)
		self.assertNotEqual(other_protocol.reference_name, import_name)

	def test_store_validation_from_import_skips_when_disabled(self):
		self._set_upload_mode(UPLOAD_MODE_NONE)
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		import_name = "EII-SKIP"
		self._attach_file_to_import(file_name, import_name)
		import_doc = frappe._dict(
			doctype="E Invoice Import",
			name=import_name,
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
		# Fixture is XRechnung; reuse key includes that profile's stylesheet hash
		self.assertEqual(original.invoice_profile, EInvoiceProfile.XRECHNUNG.value)

		second = self._make_xml_file()
		second_doc = frappe.get_doc("File", second)
		import_doc = frappe.new_doc("E Invoice Import")
		import_doc.profile = EInvoiceProfile.XRECHNUNG.value
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
		reused = reuse_validation_for_hashes(
			second_doc.content_hash,
			xml_content_hash(CII_FIXTURE.read_bytes()),
			invoice_profile=EInvoiceProfile.XRECHNUNG.value,
		)
		self.assertIsNotNone(reused)
		self.assertEqual(reused[2], original.result)

	def test_import_xrechnung_runs_en16931_as_well(self):
		# Protocol store resolves File via attachment on this import (file_url alone is not unique)
		file_name = self._make_xml_file(attached_to_doctype="E Invoice Import", attached_to_name="EII-XR-1")
		frappe.db.set_value("File", file_name, "attached_to_field", "einvoice", update_modified=False)
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

		# Protocol is written after attach (parent on_update), not in before_save
		_flush_import_protocol_payload(import_doc)

		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.result, "Invalid")
		self.assertIn("xr-err", protocol.validation_errors)
		self.assertIn("en-err", protocol.validation_errors)
		self.assertEqual(protocol.invoice_profile, EInvoiceProfile.XRECHNUNG.value)

	def test_ruleset_hash_uses_en16931_fallback_for_unknown_profile(self):
		"""Lookup hash must match Schematron's EN 16931 fallback for missing/unknown profiles."""
		en_hash = get_ruleset_hash(EInvoiceProfile.EN16931)
		self.assertEqual(_effective_cii_profile(None), EInvoiceProfile.EN16931)
		self.assertEqual(_effective_cii_profile("urn:unknown:guideline"), EInvoiceProfile.EN16931)
		self.assertEqual(_ruleset_hash_for_format(EInvoiceFormat.CII, None), en_hash)
		self.assertEqual(_ruleset_hash_for_format(EInvoiceFormat.CII, "urn:unknown:guideline"), en_hash)
		self.assertEqual(_ruleset_hash_for_format(EInvoiceFormat.UBL_INVOICE, None), "")

	def test_unknown_profile_reuses_prior_en16931_validation(self):
		"""Second upload with unrecognized guideline must copy, not re-run Schematron."""
		first = self._make_xml_file()
		create_protocol_stub_from_file(first)
		# Force unknown profile on the stub so lookup previously used an empty ruleset hash
		protocol_name = frappe.db.get_value("E Invoice Protocol", {"file": first}, "name")
		frappe.db.set_value(
			"E Invoice Protocol",
			protocol_name,
			{"invoice_profile": "urn:unknown:guideline", "ruleset_hash": ""},
			update_modified=False,
		)

		with patch(
			"eu_einvoice.european_e_invoice.protocol.get_validation_errors",
			return_value=(["only-once"], []),
		) as schematron:
			validate_protocol_for_file(first)
			schematron.assert_called()

		original = frappe.get_doc("E Invoice Protocol", protocol_name)
		self.assertEqual(original.result, "Invalid")
		self.assertEqual(original.invoice_profile, EInvoiceProfile.EN16931.value)
		self.assertEqual(original.ruleset_hash, get_ruleset_hash(EInvoiceProfile.EN16931))

		second = self._make_xml_file()
		create_protocol_stub_from_file(second)
		frappe.db.set_value(
			"E Invoice Protocol",
			frappe.db.get_value("E Invoice Protocol", {"file": second}, "name"),
			{"invoice_profile": "urn:unknown:guideline", "result": "Pending", "ruleset_hash": ""},
			update_modified=False,
		)

		with patch(
			"eu_einvoice.european_e_invoice.protocol.get_validation_errors",
			side_effect=AssertionError("Schematron must not run when effective ruleset matches"),
		):
			validate_protocol_for_file(second)

		copied = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": second}, "name")
		)
		self.assertEqual(copied.result, "Invalid")
		self.assertEqual(copied.copied_from, original.name)
		self.assertEqual(copied.validation_errors, "only-once")

	def test_import_protocol_store_waits_until_file_attached(self):
		"""before_save must not write a settled protocol without File; flush after attach."""
		file_name = self._make_xml_file()
		file_doc = frappe.get_doc("File", file_name)
		self.assertFalse(file_doc.attached_to_doctype)

		import_doc = frappe.new_doc("E Invoice Import")
		import_doc.name = "EII-DEFER-1"
		import_doc.profile = EInvoiceProfile.EN16931.value
		import_doc.einvoice = file_doc.file_url

		with patch(
			"eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.get_validation_errors",
			return_value=(["deferred-err"], []),
		):
			import_doc._validate_schematron(CII_FIXTURE.read_bytes())

		self.assertTrue(import_doc.flags.einvoice_protocol_payload)
		# No attached File yet and store not flushed: no protocol for this file
		self.assertFalse(frappe.db.exists("E Invoice Protocol", {"file": file_name, "result": "Invalid"}))

		self._attach_file_to_import(file_name, import_doc.name)
		maybe_validate_protocol_on_parent_attach(import_doc)

		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		self.assertEqual(protocol.result, "Invalid")
		self.assertEqual(protocol.validation_errors, "deferred-err")
		self.assertEqual(protocol.content_hash, file_doc.content_hash)
		self.assertEqual(protocol.reference_doctype, "E Invoice Import")
		self.assertEqual(protocol.reference_name, import_doc.name)
		self.assertIsNone(import_doc.flags.einvoice_protocol_payload)


class TestEInvoiceProtocolPermissions(IntegrationTestCase):
	"""Accounts read is scoped to linked File / reference, not role-wide.

	Uses dedicated users (not test@example.com) so System Manager is not in play.
	"""

	OWNER = "protocol_owner@example.com"
	OTHER = "protocol_other@example.com"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for email, first_name in (
			(cls.OWNER, "Protocol Owner"),
			(cls.OTHER, "Protocol Other"),
		):
			if frappe.db.exists("User", email):
				user = frappe.get_doc("User", email)
			else:
				user = frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": first_name,
						"send_welcome_email": 0,
					}
				).insert(ignore_permissions=True)
			user.add_roles("Accounts User")
			# Ensure System Manager never sneaks in on reused users
			if "System Manager" in frappe.get_roles(email):
				user.remove_roles("System Manager")

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.db.delete("E Invoice Protocol")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("E Invoice Protocol")
		super().tearDown()

	def _make_private_xml_owned_by(self, owner: str) -> str:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "protocol-perm-test.xml",
				"content": CII_FIXTURE.read_bytes(),
				"is_private": 1,
			}
		)
		with patch(
			"eu_einvoice.european_e_invoice.protocol.maybe_create_protocol_stub",
			return_value=None,
		):
			file_doc.insert(ignore_permissions=True)
		frappe.db.set_value("File", file_doc.name, "owner", owner, update_modified=False)
		return file_doc.name

	def test_accounts_user_reads_own_unattached_private_stub(self):
		from eu_einvoice.european_e_invoice.doctype.e_invoice_protocol.e_invoice_protocol import (
			has_permission,
		)

		file_name = self._make_private_xml_owned_by(self.OWNER)
		create_protocol_stub_from_file(file_name)
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)

		self.assertTrue(has_permission(protocol, "read", user=self.OWNER))
		self.assertFalse(has_permission(protocol, "read", user=self.OTHER))

	def test_list_hides_other_users_empty_company_stub(self):
		"""Empty *Company* must not make private upload stubs visible to all Accounts users."""
		file_name = self._make_private_xml_owned_by(self.OWNER)
		create_protocol_stub_from_file(file_name)
		protocol_name = frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")

		frappe.set_user(self.OTHER)
		self.assertNotIn(
			protocol_name,
			frappe.get_list("E Invoice Protocol", pluck="name", limit=100),
		)

		frappe.set_user(self.OWNER)
		self.assertIn(
			protocol_name,
			frappe.get_list("E Invoice Protocol", pluck="name", limit=100),
		)

	def test_accounts_denied_when_file_and_reference_gone(self):
		from eu_einvoice.european_e_invoice.doctype.e_invoice_protocol.e_invoice_protocol import (
			get_permission_query_conditions,
			has_permission,
		)

		file_name = self._make_private_xml_owned_by(self.OWNER)
		create_protocol_stub_from_file(file_name)
		protocol = frappe.get_doc(
			"E Invoice Protocol", frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
		)
		frappe.db.set_value(
			"E Invoice Protocol",
			protocol.name,
			{"file": None, "reference_doctype": None, "reference_name": None},
			update_modified=False,
		)
		protocol.reload()

		self.assertFalse(has_permission(protocol, "read", user=self.OWNER))
		self.assertTrue(has_permission(protocol, "read", user="Administrator"))
		self.assertEqual(get_permission_query_conditions("Administrator"), "")

	def test_read_allowed_via_readable_reference_without_file(self):
		from eu_einvoice.european_e_invoice.doctype.e_invoice_protocol.e_invoice_protocol import (
			has_permission,
		)

		frappe.get_doc("User", self.OWNER).add_roles("Purchase User")
		company = frappe.db.get_value("Company", {}, "name")
		import_doc = frappe.get_doc({"doctype": "E Invoice Import", "company": company}).insert(
			ignore_permissions=True
		)
		self.addCleanup(lambda: frappe.delete_doc("E Invoice Import", import_doc.name, force=True))

		protocol = frappe.get_doc(
			{
				"doctype": "E Invoice Protocol",
				"result": "Pending",
				"reference_doctype": "E Invoice Import",
				"reference_name": import_doc.name,
				"company": company,
			}
		)
		protocol.flags.ignore_links = True
		protocol.insert(ignore_permissions=True)

		self.assertTrue(has_permission(protocol, "read", user=self.OWNER))
		self.assertFalse(has_permission(protocol, "read", user=self.OTHER))

	def test_list_includes_protocol_readable_via_reference_without_file(self):
		"""Desk list must match has_permission when a readable reference exists without a File."""
		frappe.get_doc("User", self.OWNER).add_roles("Purchase User")
		company = frappe.db.get_value("Company", {}, "name")
		import_doc = frappe.get_doc({"doctype": "E Invoice Import", "company": company}).insert(
			ignore_permissions=True
		)
		self.addCleanup(lambda: frappe.delete_doc("E Invoice Import", import_doc.name, force=True))

		protocol = frappe.get_doc(
			{
				"doctype": "E Invoice Protocol",
				"result": "Pending",
				"reference_doctype": "E Invoice Import",
				"reference_name": import_doc.name,
				"company": company,
			}
		)
		protocol.flags.ignore_links = True
		protocol.insert(ignore_permissions=True)

		frappe.set_user(self.OWNER)
		self.assertIn(
			protocol.name,
			frappe.get_list("E Invoice Protocol", pluck="name", limit=100),
		)

		frappe.set_user(self.OTHER)
		self.assertNotIn(
			protocol.name,
			frappe.get_list("E Invoice Protocol", pluck="name", limit=100),
		)

	def test_list_hides_protocol_when_reference_gone(self):
		"""List must not match on reference_doctype alone after the reference document is deleted."""
		from eu_einvoice.european_e_invoice.doctype.e_invoice_protocol.e_invoice_protocol import (
			has_permission,
		)

		frappe.get_doc("User", self.OWNER).add_roles("Purchase User")
		company = frappe.db.get_value("Company", {}, "name")
		import_doc = frappe.get_doc({"doctype": "E Invoice Import", "company": company}).insert(
			ignore_permissions=True
		)

		protocol = frappe.get_doc(
			{
				"doctype": "E Invoice Protocol",
				"result": "Pending",
				"reference_doctype": "E Invoice Import",
				"reference_name": import_doc.name,
				"company": company,
			}
		)
		protocol.flags.ignore_links = True
		protocol.insert(ignore_permissions=True)

		frappe.delete_doc("E Invoice Import", import_doc.name, force=True, ignore_permissions=True)
		protocol.reload()

		self.assertFalse(has_permission(protocol, "read", user=self.OWNER))
		frappe.set_user(self.OWNER)
		self.assertNotIn(
			protocol.name,
			frappe.get_list("E Invoice Protocol", pluck="name", limit=100),
		)
