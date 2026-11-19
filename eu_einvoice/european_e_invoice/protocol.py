"""E Invoice Protocol: attach-time Schematron validation for Import and Purchase Invoice."""

from __future__ import annotations

import hashlib
from pathlib import Path

import frappe
from frappe.utils import now_datetime
from lxml.etree import XMLParser, fromstring

from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import (
	_file_content_as_bytes,
)
from eu_einvoice.saxon import UnsafeXMLError
from eu_einvoice.schematron import (
	PROFILE_TO_XSL,
	get_ruleset_hash,
	get_validation_errors,
	get_validator_version,
)
from eu_einvoice.utils import EInvoiceProfile, get_profile
from eu_einvoice.visualization import EInvoiceFormat, detect_einvoice_format

# Untrusted invoice XML: never resolve DTDs / entities when reading profile labels.
_SAFE_XML_PARSER = XMLParser(
	resolve_entities=False,
	load_dtd=False,
	dtd_validation=False,
	no_network=True,
)

# Parents that receive attach-time protocol validation (hard-coded; no settings table).
PROTOCOL_PARENT_DOCTYPES = ("E Invoice Import", "Purchase Invoice")

XML_EXTENSIONS = {".xml"}
PDF_EXTENSIONS = {".pdf"}
XML_MIME_PREFIXES = ("application/xml", "text/xml")
PDF_MIME_PREFIXES = ("application/pdf",)

VALIDATOR_NAME = "Schematron (SaxonC)"


def delete_protocol_for_file(doc, method=None):
	"""File.on_trash: drop the 1:1 protocol row when its **File** is deleted.

	Must never raise - File deletes must not fail.
	"""
	try:
		name = frappe.db.get_value("E Invoice Protocol", {"file": doc.name}, "name")
		if name:
			frappe.delete_doc("E Invoice Protocol", name, ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title="E Invoice protocol delete (file trash)",
			reference_doctype="File",
			reference_name=getattr(doc, "name", None),
		)


def maybe_validate_protocol_on_attach(doc, method=None):
	"""File.on_update: run Schematron when attached to Import or Purchase Invoice.

	Must never raise - File hooks must not fail saves.
	Does not cover owned orphans attached via Attach fields (those use
	``frappe.db.set_value`` with no File events) - see
	``maybe_validate_protocol_on_parent_attach``.
	"""
	try:
		_maybe_validate_protocol_on_attach(doc)
	except Exception:
		frappe.log_error(
			title="E Invoice protocol validate (hook)",
			reference_doctype="File",
			reference_name=getattr(doc, "name", None),
		)


def maybe_validate_protocol_on_parent_attach(doc, method=None):
	"""Parent on_update: validate Files linked via Attach after Frappe attaches them.

	Frappe's ``attach_files_to_document`` owns orphans with ``frappe.db.set_value``,
	which does not run File events. Registered only for Import and Purchase Invoice.
	Must never raise - parent saves must not fail.
	"""
	try:
		_maybe_validate_protocol_on_parent_attach(doc)
	except Exception:
		frappe.log_error(
			title="E Invoice protocol validate (parent attach)",
			reference_doctype=getattr(doc, "doctype", None),
			reference_name=getattr(doc, "name", None),
		)


def _maybe_validate_protocol_on_parent_attach(doc):
	# **E Invoice Import**: flush Schematron results after attach_files (before_save is too early).
	# Do not enqueue attach-time Schematron for the *E Invoice* (`einvoice`) field file.
	if doc.doctype == "E Invoice Import":
		_flush_import_protocol_payload(doc)
		return

	if doc.doctype not in PROTOCOL_PARENT_DOCTYPES:
		return

	file_urls = _changed_attach_urls(doc)
	if not file_urls:
		return

	file_names = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"file_url": ("in", file_urls),
		},
		pluck="name",
	)
	for file_name in file_names:
		_maybe_validate_protocol_on_attach(frappe.get_doc("File", file_name))


def _flush_import_protocol_payload(doc):
	"""Persist deferred import validation onto the File attached in this save."""
	payload = getattr(doc.flags, "einvoice_protocol_payload", None)
	if not payload:
		return
	store_validation_from_import(doc, payload["xml_bytes"], payload["errors"], payload["warnings"])
	doc.flags.einvoice_protocol_payload = None


def _changed_attach_urls(doc) -> list[str]:
	"""Attach / Attach Image values that changed on this save (parent and child tables).

	Matches Frappe's ``attach_files_to_document``, which also adopts orphans from
	child-table Attach fields via ``db.set_value`` (no File events).
	"""
	urls: list[str] = []

	def _collect(container, fieldname: str):
		if not container.has_value_changed(fieldname):
			return
		value = container.get(fieldname) or ""
		if value.startswith(("/files", "/private/files", "http://", "https://")):
			urls.append(value)

	for df in doc.meta.get("fields", {"fieldtype": ["in", ["Attach", "Attach Image"]]}):
		_collect(doc, df.fieldname)

	for table_df in doc.meta.get("fields", {"fieldtype": "Table"}):
		child_rows = doc.get(table_df.fieldname) or []
		if not child_rows:
			continue
		child_meta = frappe.get_meta(table_df.options)
		child_attach_fields = child_meta.get("fields", {"fieldtype": ["in", ["Attach", "Attach Image"]]})
		if not child_attach_fields:
			continue
		for child_row in child_rows:
			for child_df in child_attach_fields:
				_collect(child_row, child_df.fieldname)

	return urls


def _maybe_validate_protocol_on_attach(doc):
	if doc.is_folder or doc.is_remote_file:
		return
	if not doc.attached_to_doctype or not doc.attached_to_name:
		return
	if doc.attached_to_doctype not in PROTOCOL_PARENT_DOCTYPES:
		return

	# Import *E Invoice* (`einvoice`) is filled from `_validate_schematron` after attach.
	# Other files on the same import (sidebar) still need attach-time validation.
	if doc.attached_to_doctype == "E Invoice Import" and _is_import_einvoice_file(doc):
		return

	existing = _find_protocol_for_file(doc.name)
	if existing:
		# Any existing protocol finishes validation; Schematron is not re-run on re-attach.
		_update_protocol_reference(existing, doc)
		return

	kind = _candidate_kind(doc)
	if not kind:
		return

	# Always queue Schematron (XML and PDF) - validation is too slow for Desk requests
	_enqueue_for_file(
		"eu_einvoice.european_e_invoice.protocol.validate_protocol_for_file",
		doc.name,
	)


def _is_import_einvoice_file(file_doc) -> bool:
	"""True when this File is the import's *E Invoice* (`einvoice`) Attach field."""
	if file_doc.attached_to_field == "einvoice":
		return True
	if file_doc.attached_to_field:
		return False
	# Sidebar attach leaves attached_to_field empty; only skip when URL matches einvoice.
	einvoice_url = frappe.db.get_value("E Invoice Import", file_doc.attached_to_name, "einvoice")
	return bool(einvoice_url and einvoice_url == file_doc.file_url)


def validate_protocol_for_file(file_name: str):
	"""Create or update the protocol for this File and run Schematron once."""
	try:
		_validate_protocol_for_file(file_name)
	except Exception:
		frappe.log_error(
			title="E Invoice protocol validation",
			reference_doctype="File",
			reference_name=file_name,
		)


def _validate_protocol_for_file(file_name: str):
	file_doc = frappe.get_doc("File", file_name)

	xml_bytes = _read_xml_bytes_quiet(file_doc)
	if not xml_bytes:
		return

	einvoice_format = detect_einvoice_format(xml_bytes)
	if not einvoice_format:
		return

	protocol = _find_protocol_for_file(file_name)
	if protocol:
		_update_protocol_reference(protocol, file_doc)
		return

	profile_label = _cheap_profile(xml_bytes, einvoice_format)
	profile = _effective_cii_profile(profile_label) if einvoice_format == EInvoiceFormat.CII else None

	# Schematron in this app is CII-oriented; UBL etc. cannot be checked yet
	if einvoice_format != EInvoiceFormat.CII or profile is None:
		if not protocol:
			protocol = _new_protocol_doc(file_doc, xml_bytes, einvoice_format, profile_label)
		_mark_unsupported(protocol, einvoice_format)
		if file_doc.attached_to_doctype:
			protocol.reference_doctype = file_doc.attached_to_doctype
			protocol.reference_name = file_doc.attached_to_name
			protocol.company = _company_from_reference(
				file_doc.attached_to_doctype, file_doc.attached_to_name
			)
		if protocol.is_new():
			_insert_protocol_doc(protocol)
		else:
			protocol.flags.ignore_links = True
			protocol.save(ignore_permissions=True)
		return

	xml_string = xml_bytes.decode("utf-8")
	try:
		errors, warnings = get_validation_errors(xml_string, profile)
		if profile == EInvoiceProfile.XRECHNUNG:
			basic_errors, basic_warnings = get_validation_errors(xml_string, EInvoiceProfile.EN16931)
			errors = list(errors) + list(basic_errors)
			warnings = list(warnings) + list(basic_warnings)
	except UnsafeXMLError as exc:
		# detect_einvoice_format may still accept DOCTYPE XML; record Invalid instead of
		# only writing an Error Log (and formerly leaving a Pending stub).
		store_validation_result(
			file_doc=file_doc,
			xml_bytes=xml_bytes,
			einvoice_format=einvoice_format.value,
			invoice_profile=profile.value,
			errors=[str(exc)],
			warnings=[],
		)
		return

	store_validation_result(
		file_doc=file_doc,
		xml_bytes=xml_bytes,
		einvoice_format=einvoice_format.value,
		invoice_profile=profile.value,
		errors=errors,
		warnings=warnings,
	)


def store_validation_result(
	*,
	file_doc=None,
	file_url: str | None = None,
	xml_bytes: bytes,
	einvoice_format: str | None,
	invoice_profile: str | None,
	errors: list[str],
	warnings: list[str],
	reference_doctype: str | None = None,
	reference_name: str | None = None,
):
	"""Persist Schematron results onto this File's protocol (create or update)."""
	file_name = None
	attached_doctype = reference_doctype
	attached_name = reference_name
	display_name = None
	content_hash = None

	if file_doc:
		file_name = file_doc.name
		display_name = file_doc.file_name
		content_hash = file_doc.content_hash
		attached_doctype = attached_doctype or file_doc.attached_to_doctype
		attached_name = attached_name or file_doc.attached_to_name
	elif file_url:
		# file_url is not unique (attachment copies reuse it) - only resolve via reference
		file_row = _find_attached_file_row(
			file_url,
			attached_to_doctype=attached_doctype,
			attached_to_name=attached_name,
		)
		if file_row:
			file_name = file_row.name
			content_hash = file_row.content_hash
			display_name = file_row.file_name
		else:
			display_name = Path(file_url).name

	file_hash = _sha256(xml_bytes)
	ruleset_hash = get_ruleset_hash(invoice_profile) if invoice_profile else ""

	stylesheet = ""
	if invoice_profile:
		try:
			stylesheet = PROFILE_TO_XSL.get(EInvoiceProfile(invoice_profile), "")
		except ValueError:
			stylesheet = ""

	validator_label = VALIDATOR_NAME
	if stylesheet:
		validator_label = f"{VALIDATOR_NAME} / {stylesheet}"

	values = {
		"result": "Invalid" if errors else "Valid",
		"validation_timestamp": now_datetime(),
		"einvoice_format": einvoice_format,
		"invoice_profile": invoice_profile,
		"file": file_name,
		"file_name": display_name,
		"content_hash": content_hash,
		"file_hash": file_hash,
		"ruleset_hash": ruleset_hash,
		"reference_doctype": attached_doctype,
		"reference_name": attached_name,
		"company": _company_from_reference(attached_doctype, attached_name),
		"validator": validator_label,
		"validator_version": get_validator_version(invoice_profile),
		"error_count": len(errors),
		"warning_count": len(warnings),
		"message_count": len(errors) + len(warnings),
		"validation_errors": "\n".join(errors) if errors else "",
		"validation_warnings": "\n".join(warnings) if warnings else "",
	}

	def _persist():
		protocol = _find_protocol_for_file(file_name) if file_name else None
		if protocol:
			protocol.update(values)
			if not protocol.detection_timestamp:
				protocol.detection_timestamp = now_datetime()
			protocol.flags.ignore_links = True
			protocol.save(ignore_permissions=True)
			return protocol

		doc = frappe.new_doc("E Invoice Protocol")
		doc.detection_timestamp = now_datetime()
		doc.update(values)
		return _insert_protocol_doc(doc)

	return _persist()


def store_validation_from_import(import_doc, xml_bytes: bytes, errors: list[str], warnings: list[str]):
	"""Persist import Schematron results onto the File for *E Invoice* (`einvoice`).

	Call after ``attach_files_to_document`` (parent ``on_update`` hook). Looking up
	the File in ``before_save`` is too early: Frappe attaches owned uploads then.
	"""
	file_url = import_doc.get("einvoice")
	# Prefer the File attached to this import - file_url alone can match another doc's copy
	file_doc = _find_attached_file(
		file_url,
		attached_to_doctype=import_doc.doctype,
		attached_to_name=import_doc.name,
		attached_to_field="einvoice",
	)
	# attach_files may still be pending in tests that call this directly: use the orphan.
	if not file_doc:
		file_doc = _find_orphan_file(file_url)

	einvoice_format = detect_einvoice_format(xml_bytes)
	store_validation_result(
		file_doc=file_doc,
		file_url=file_url,
		xml_bytes=xml_bytes,
		einvoice_format=einvoice_format.value if einvoice_format else None,
		invoice_profile=import_doc.get("profile"),
		errors=list(errors) if errors else [],
		warnings=list(warnings) if warnings else [],
		reference_doctype=import_doc.doctype,
		reference_name=import_doc.name,
	)


def _new_protocol_doc(file_doc, xml_bytes: bytes, einvoice_format: EInvoiceFormat, profile_label: str | None):
	"""Build an unsaved protocol shell; caller must set *Result* before insert."""
	doc = frappe.new_doc("E Invoice Protocol")
	doc.detection_timestamp = now_datetime()
	doc.einvoice_format = einvoice_format.value
	doc.invoice_profile = profile_label
	doc.file = file_doc.name
	doc.file_name = file_doc.file_name
	doc.content_hash = file_doc.content_hash
	doc.file_hash = _sha256(xml_bytes)
	doc.reference_doctype = file_doc.attached_to_doctype
	doc.reference_name = file_doc.attached_to_name
	doc.company = _company_from_reference(doc.reference_doctype, doc.reference_name)
	return doc


def _mark_unsupported(protocol, einvoice_format: EInvoiceFormat):
	"""Mark a protocol as Unsupported when Schematron cannot run for this format."""
	protocol.result = "Unsupported"
	protocol.validation_timestamp = now_datetime()
	protocol.einvoice_format = einvoice_format.value
	protocol.ruleset_hash = ""
	protocol.validator = VALIDATOR_NAME
	protocol.validator_version = ""
	protocol.error_count = 0
	protocol.warning_count = 0
	protocol.message_count = 0
	protocol.validation_errors = ""
	protocol.validation_warnings = ""


def _update_protocol_reference(protocol, file_doc):
	if not file_doc.attached_to_doctype:
		return
	protocol.db_set(
		{
			"reference_doctype": file_doc.attached_to_doctype,
			"reference_name": file_doc.attached_to_name,
			"company": _company_from_reference(file_doc.attached_to_doctype, file_doc.attached_to_name),
		},
		update_modified=False,
	)


def _company_from_reference(doctype: str | None, name: str | None) -> str | None:
	"""Return *Company* from the reference document when that DocType has `company`."""
	if not doctype or not name:
		return None
	if not frappe.get_meta(doctype).has_field("company"):
		return None
	return frappe.db.get_value(doctype, name, "company")


def _effective_cii_profile(profile_label: str | EInvoiceProfile | None) -> EInvoiceProfile:
	"""Profile used for Schematron: missing / unrecognized labels fall back to EN 16931."""
	if isinstance(profile_label, EInvoiceProfile):
		return profile_label
	try:
		return EInvoiceProfile(profile_label) if profile_label else EInvoiceProfile.EN16931
	except ValueError:
		return EInvoiceProfile.EN16931


def _ruleset_hash_for_format(einvoice_format: EInvoiceFormat, profile: str | EInvoiceProfile | None) -> str:
	"""Ruleset identity for audit; empty when Schematron does not apply (non-CII)."""
	if einvoice_format != EInvoiceFormat.CII:
		return ""
	return get_ruleset_hash(_effective_cii_profile(profile))


def _candidate_kind(doc) -> str | None:
	"""Return 'xml', 'pdf', or None based on file name / type."""
	name = (doc.file_name or "").lower()
	content_type = (getattr(doc, "content_type", None) or "").lower()
	suffix = Path(name).suffix.lower() if name else ""

	if suffix in XML_EXTENSIONS or any(content_type.startswith(p) for p in XML_MIME_PREFIXES):
		return "xml"
	if suffix in PDF_EXTENSIONS or any(content_type.startswith(p) for p in PDF_MIME_PREFIXES):
		return "pdf"
	return None


def _read_xml_bytes_quiet(file_doc) -> bytes | None:
	"""Extract XML without throwing user-facing Import dialogs. Never raises."""
	try:
		content = _file_content_as_bytes(file_doc)
		name = (file_doc.file_name or file_doc.file_url or "").lower()
		suffix = Path(name).suffix
		if suffix == ".xml":
			return content
		if suffix == ".pdf":
			from facturx import get_xml_from_pdf

			_xml_filename, xml_bytes = get_xml_from_pdf(content, check_xsd=False)
			return xml_bytes or None
	except Exception:
		return None
	return None


def _cheap_profile(xml_bytes: bytes, einvoice_format: EInvoiceFormat) -> str | None:
	"""Read profile / guideline without Schematron."""
	try:
		root = fromstring(xml_bytes, parser=_SAFE_XML_PARSER)
	except Exception:
		return None

	if einvoice_format == EInvoiceFormat.CII:
		# ram:GuidelineSpecifiedDocumentContextParameter / ram:ID
		ns = {
			"rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
			"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
		}
		nodes = root.xpath(
			".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID/text()",
			namespaces=ns,
		)
		if nodes:
			profile = get_profile(str(nodes[0]).strip())
			return profile.value if profile else str(nodes[0]).strip()
		return None

	# UBL: cbc:ProfileID or cbc:CustomizationID
	ns = {
		"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
	}
	for path in (".//cbc:ProfileID/text()", ".//cbc:CustomizationID/text()"):
		nodes = root.xpath(path, namespaces=ns)
		if nodes:
			return str(nodes[0]).strip()
	return None


def _find_protocol_for_file(file_name: str | None):
	if not file_name:
		return None
	name = frappe.db.get_value("E Invoice Protocol", {"file": file_name}, "name")
	return frappe.get_doc("E Invoice Protocol", name) if name else None


def _enqueue_for_file(method: str, file_name: str):
	"""Enqueue a per-File job; dedupe so workers do not pile up for the same file."""
	frappe.enqueue(
		method,
		queue="short",
		enqueue_after_commit=True,
		file_name=file_name,
		job_id=f"einvoice-protocol-validate-{file_name}",
		deduplicate=True,
	)


def _insert_protocol_doc(doc):
	"""Insert protocol; on unique `file` race, return the row the other worker created."""
	doc.flags.ignore_links = True
	try:
		doc.insert(ignore_permissions=True)
		return doc
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		existing = _find_protocol_for_file(doc.file)
		if existing:
			return existing
		raise


def _find_attached_file_row(
	file_url: str | None,
	*,
	attached_to_doctype: str | None,
	attached_to_name: str | None,
	attached_to_field: str | None = None,
):
	"""Return File name/content_hash/file_name for this URL on the given document.

	``file_url`` is not unique: Frappe's ``create_attachment_copy`` reuses the URL on a
	new File row. Never pick an arbitrary same-URL File belonging to another document.
	"""
	if not file_url or not attached_to_doctype or not attached_to_name:
		return None

	filters = {
		"file_url": file_url,
		"attached_to_doctype": attached_to_doctype,
		"attached_to_name": attached_to_name,
	}
	if attached_to_field:
		filters["attached_to_field"] = attached_to_field

	row = frappe.db.get_value("File", filters, ["name", "content_hash", "file_name"], as_dict=True)
	if row or not attached_to_field:
		return row

	filters.pop("attached_to_field")
	return frappe.db.get_value("File", filters, ["name", "content_hash", "file_name"], as_dict=True)


def _find_attached_file(
	file_url: str | None,
	*,
	attached_to_doctype: str | None,
	attached_to_name: str | None,
	attached_to_field: str | None = None,
):
	row = _find_attached_file_row(
		file_url,
		attached_to_doctype=attached_to_doctype,
		attached_to_name=attached_to_name,
		attached_to_field=attached_to_field,
	)
	return frappe.get_doc("File", row.name) if row else None


def _find_orphan_file(file_url: str | None):
	"""Owned unattached File for this URL (same filters as Frappe attach_files)."""
	if not file_url:
		return None
	name = frappe.db.get_value(
		"File",
		{
			"file_url": file_url,
			"attached_to_doctype": ["is", "not set"],
			"attached_to_name": ["is", "not set"],
			"attached_to_field": ["is", "not set"],
			"owner": frappe.session.user,
		},
		"name",
	)
	return frappe.get_doc("File", name) if name else None


def _sha256(data: bytes) -> str:
	return hashlib.sha256(data).hexdigest()
