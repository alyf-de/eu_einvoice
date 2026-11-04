"""E Invoice Protocol: upload stubs and attach-time validation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import frappe
from frappe.utils import now_datetime
from lxml.etree import fromstring

from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import (
	_file_content_as_bytes,
)
from eu_einvoice.schematron import (
	PROFILE_TO_XSL,
	get_ruleset_hash,
	get_validation_errors,
	get_validator_version,
)
from eu_einvoice.utils import EInvoiceProfile, get_profile
from eu_einvoice.visualization import EInvoiceFormat, detect_einvoice_format

UPLOAD_MODE_NONE = "None"
UPLOAD_MODE_SELECTED = "Only selected DocTypes"
UPLOAD_MODE_ALL = "All files"

DEFAULT_PROTOCOL_DOCTYPES = ("E Invoice Import", "Purchase Invoice")

XML_EXTENSIONS = {".xml"}
PDF_EXTENSIONS = {".pdf"}
XML_MIME_PREFIXES = ("application/xml", "text/xml")
PDF_MIME_PREFIXES = ("application/pdf",)

VALIDATOR_NAME = "Schematron (SaxonC)"

# Serialize protocol create / Schematron across workers (MariaDB GET_LOCK)
FILE_LOCK_TIMEOUT = 120
HASH_LOCK_TIMEOUT = 300

# Results that finish the validation phase (copied/propagated; Schematron not re-run)
SETTLED_RESULTS = ("Valid", "Invalid", "Unsupported")

# Fields copied from an already-validated protocol for the same hashes + ruleset
_COPIED_VALIDATION_FIELDS = (
	"result",
	"validation_timestamp",
	"einvoice_format",
	"invoice_profile",
	"ruleset_hash",
	"validator",
	"validator_version",
	"error_count",
	"warning_count",
	"message_count",
	"validation_errors",
	"validation_warnings",
)


def ensure_default_protocol_doctypes():
	"""Seed default Protocol DocTypes on **E Invoice Settings** when the table is empty."""
	if not frappe.db.exists("DocType", "E Invoice Settings"):
		return
	if not frappe.db.exists("DocType", "E Invoice Protocol DocType"):
		return

	settings = frappe.get_single("E Invoice Settings")
	if settings.get("protocol_doctypes"):
		return

	for doctype in DEFAULT_PROTOCOL_DOCTYPES:
		settings.append("protocol_doctypes", {"document_type": doctype})
	settings.flags.ignore_permissions = True
	settings.save()


def maybe_create_protocol_stub(doc, method=None):
	"""File.after_insert: optional lightweight protocol stub (no Schematron).

	Must never raise - File hooks must not fail uploads.
	"""
	try:
		_maybe_create_protocol_stub(doc)
	except Exception:
		frappe.log_error(
			title="E Invoice protocol stub (hook)",
			reference_doctype="File",
			reference_name=getattr(doc, "name", None),
		)


def _maybe_create_protocol_stub(doc):
	if doc.is_folder or doc.is_remote_file:
		return

	settings = _get_protocol_settings()
	if settings["create_protocol_on_upload"] == UPLOAD_MODE_NONE:
		return

	if settings["create_protocol_on_upload"] == UPLOAD_MODE_SELECTED:
		if doc.attached_to_doctype not in settings["protocol_doctypes"]:
			return

	kind = _candidate_kind(doc)
	if not kind:
		return

	# PDF and XML: detect/queue off the request so uploads stay fast and safe
	_enqueue_for_file(
		"eu_einvoice.european_e_invoice.protocol.create_protocol_stub_from_file",
		doc.name,
		job_suffix="stub",
	)


def maybe_validate_protocol_on_attach(doc, method=None):
	"""File.on_update: run Schematron when attached to a configured DocType.

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
	which does not run File events. Register on ``*`` so this runs after that hook.
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
	if doc.doctype in ("File", "E Invoice Protocol", "E Invoice Import"):
		return

	settings = _get_protocol_settings()
	if settings["create_protocol_on_upload"] == UPLOAD_MODE_NONE:
		return
	if doc.doctype not in settings["protocol_doctypes"]:
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


def _changed_attach_urls(doc) -> list[str]:
	"""Attach / Attach Image field values that changed on this save."""
	urls: list[str] = []
	for df in doc.meta.get("fields", {"fieldtype": ["in", ["Attach", "Attach Image"]]}):
		if not doc.has_value_changed(df.fieldname):
			continue
		value = doc.get(df.fieldname) or ""
		if value.startswith(("/files", "/private/files", "http://", "https://")):
			urls.append(value)
	return urls


def _maybe_validate_protocol_on_attach(doc):
	if doc.is_folder or doc.is_remote_file:
		return
	if not doc.attached_to_doctype or not doc.attached_to_name:
		return

	settings = _get_protocol_settings()
	if settings["create_protocol_on_upload"] == UPLOAD_MODE_NONE:
		return
	if doc.attached_to_doctype not in settings["protocol_doctypes"]:
		return

	# **E Invoice Import** fills the protocol from `_validate_schematron` (same request).
	if doc.attached_to_doctype == "E Invoice Import":
		return

	existing = _find_protocol_for_file(doc.name)
	if existing and existing.result in SETTLED_RESULTS:
		_update_protocol_reference(existing, doc)
		return

	kind = _candidate_kind(doc)
	if not kind:
		return

	# Always queue Schematron (XML and PDF) - validation is too slow for Desk requests
	_enqueue_for_file(
		"eu_einvoice.european_e_invoice.protocol.validate_protocol_for_file",
		doc.name,
		job_suffix="validate",
	)


def create_protocol_stub_from_file(file_name: str):
	"""Detect e-invoice XML and insert a protocol row for this File (1:1)."""
	try:
		return _create_protocol_stub_from_file(file_name)
	except Exception:
		frappe.log_error(
			title="E Invoice protocol stub",
			reference_doctype="File",
			reference_name=file_name,
		)
		return None


def _create_protocol_stub_from_file(file_name: str):
	file_doc = frappe.get_doc("File", file_name)
	if file_doc.is_folder or file_doc.is_remote_file:
		return

	xml_bytes = _read_xml_bytes_quiet(file_doc)
	if not xml_bytes:
		return

	einvoice_format = detect_einvoice_format(xml_bytes)
	if not einvoice_format:
		return

	with frappe.db.advisory_lock(_file_lock_key(file_name), timeout=FILE_LOCK_TIMEOUT):
		existing = _find_protocol_for_file(file_name)
		if existing:
			return existing
		return _insert_protocol_stub(file_doc, xml_bytes, einvoice_format)


def validate_protocol_for_file(file_name: str):
	"""Run Schematron for a File and store results on its protocol row (or copy)."""
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

	content_hash = file_doc.content_hash
	file_hash = _sha256(xml_bytes)
	protocol = _ensure_protocol_for_file(file_doc)
	if not protocol:
		return

	if protocol.result in SETTLED_RESULTS:
		_update_protocol_reference(protocol, file_doc)
		return

	profile_for_rules = protocol.invoice_profile or _cheap_profile(xml_bytes, einvoice_format)
	ruleset_hash = _ruleset_hash_for_format(einvoice_format, profile_for_rules)

	# Serialize Schematron / copy for the same hashes so concurrent workers do not double-run
	with frappe.db.advisory_lock(
		_hash_lock_key(content_hash, file_hash, ruleset_hash),
		timeout=HASH_LOCK_TIMEOUT,
	):
		protocol.reload()
		if protocol.result in SETTLED_RESULTS:
			_update_protocol_reference(protocol, file_doc)
			return

		# Copy from an original validation for the same hashes + ruleset
		source = _find_original_validation(content_hash, file_hash, ruleset_hash, exclude=protocol.name)
		if source:
			_apply_copied_validation(protocol, source)
			_update_protocol_reference(protocol, file_doc)
			protocol.flags.ignore_links = True
			protocol.save(ignore_permissions=True)
			return

		# Schematron in this app is CII-oriented; UBL etc. cannot be checked yet
		if einvoice_format != EInvoiceFormat.CII:
			_mark_unsupported(protocol, einvoice_format)
			_update_protocol_reference(protocol, file_doc)
			protocol.flags.ignore_links = True
			protocol.save(ignore_permissions=True)
			return

		profile_label = protocol.invoice_profile or _cheap_profile(xml_bytes, einvoice_format)
		try:
			profile = EInvoiceProfile(profile_label) if profile_label else EInvoiceProfile.EN16931
		except ValueError:
			profile = EInvoiceProfile.EN16931

		xml_string = xml_bytes.decode("utf-8")
		errors, warnings = get_validation_errors(xml_string, profile)
		if profile == EInvoiceProfile.XRECHNUNG:
			basic_errors, basic_warnings = get_validation_errors(xml_string, EInvoiceProfile.EN16931)
			errors = list(errors) + list(basic_errors)
			warnings = list(warnings) + list(basic_warnings)

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

	def _persist():
		protocol = _find_protocol_for_file(file_name) if file_name else None

		# Prefer copying an existing original validation for these hashes + ruleset
		if not protocol or protocol.result == "Pending":
			source = _find_original_validation(
				content_hash,
				file_hash,
				ruleset_hash,
				exclude=protocol.name if protocol else None,
			)
			if source:
				company = _company_from_reference(attached_doctype, attached_name)
				if not protocol:
					protocol = frappe.new_doc("E Invoice Protocol")
					protocol.detection_timestamp = now_datetime()
					protocol.file = file_name
					protocol.file_name = display_name
					protocol.content_hash = content_hash
					protocol.file_hash = file_hash
					protocol.reference_doctype = attached_doctype
					protocol.reference_name = attached_name
					protocol.company = company
					_apply_copied_validation(protocol, source)
					return _insert_protocol_doc(protocol)
				_apply_copied_validation(protocol, source)
				protocol.reference_doctype = attached_doctype
				protocol.reference_name = attached_name
				protocol.company = company
				protocol.flags.ignore_links = True
				protocol.save(ignore_permissions=True)
				return protocol

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
			"copied_from": None,
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

	if file_name:
		with frappe.db.advisory_lock(_file_lock_key(file_name), timeout=FILE_LOCK_TIMEOUT):
			return _persist()
	return _persist()


def store_validation_from_import(import_doc, xml_bytes: bytes, errors: list[str], warnings: list[str]):
	"""Called after **E Invoice Import** `_validate_schematron` (reuse its results)."""
	settings = _get_protocol_settings()
	if settings["create_protocol_on_upload"] == UPLOAD_MODE_NONE:
		return
	if "E Invoice Import" not in settings["protocol_doctypes"]:
		return

	file_url = import_doc.get("einvoice")
	# Prefer the File attached to this import - file_url alone can match another doc's copy
	file_doc = _find_attached_file(
		file_url,
		attached_to_doctype=import_doc.doctype,
		attached_to_name=import_doc.name,
		attached_to_field="einvoice",
	)

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


def xml_content_hash(xml_bytes: bytes) -> str:
	"""SHA-256 of XML bytes (stored as protocol file_hash)."""
	return _sha256(xml_bytes)


def reuse_validation_for_hashes(
	content_hash: str | None,
	file_hash: str | None,
	*,
	ruleset_hash: str | None = None,
	invoice_profile: str | None = None,
) -> tuple[list[str], list[str], str] | None:
	"""Return (errors, warnings, result) from an original protocol for these hashes + ruleset."""
	if ruleset_hash is None:
		ruleset_hash = get_ruleset_hash(invoice_profile) if invoice_profile else ""
	protocol = _find_original_validation(content_hash, file_hash, ruleset_hash)
	if not protocol:
		return None
	errors = [line for line in (protocol.validation_errors or "").split("\n") if line]
	warnings = [line for line in (protocol.validation_warnings or "").split("\n") if line]
	return errors, warnings, protocol.result


def _ensure_protocol_for_file(file_doc):
	"""Return the protocol for this File, creating a stub if needed (file-locked)."""
	with frappe.db.advisory_lock(_file_lock_key(file_doc.name), timeout=FILE_LOCK_TIMEOUT):
		protocol = _find_protocol_for_file(file_doc.name)
		if protocol:
			return protocol

		xml_bytes = _read_xml_bytes_quiet(file_doc)
		if not xml_bytes:
			return None
		einvoice_format = detect_einvoice_format(xml_bytes)
		if not einvoice_format:
			return None
		return _insert_protocol_stub(file_doc, xml_bytes, einvoice_format)


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
	protocol.copied_from = None


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


def propagate_validation_to_pending_siblings(source):
	"""Copy validation onto other Pending protocols with the same content + file + ruleset hashes.

	Covers the case where a File Manager upload left a Pending stub, then attaching
	the same blob created a new **File** that was validated.
	"""
	if getattr(source.flags, "skip_validation_propagate", False):
		return
	if source.result not in SETTLED_RESULTS:
		return
	if not source.content_hash or not source.file_hash:
		return

	original = _resolve_original(source)
	# Pending stubs have no ruleset_hash yet; match on content + file hash only.
	pending_names = frappe.get_all(
		"E Invoice Protocol",
		filters={
			"content_hash": original.content_hash,
			"file_hash": original.file_hash,
			"result": "Pending",
			"name": ("!=", source.name),
		},
		pluck="name",
	)
	for name in pending_names:
		if name == original.name:
			continue
		target = frappe.get_doc("E Invoice Protocol", name)
		_apply_copied_validation(target, original)
		target.flags.ignore_links = True
		target.flags.skip_validation_propagate = True
		target.save(ignore_permissions=True)


def _apply_copied_validation(target, source):
	"""Copy validation fields from source; set copied_from to the original protocol."""
	original = _resolve_original(source)
	for fieldname in _COPIED_VALIDATION_FIELDS:
		target.set(fieldname, original.get(fieldname))
	target.copied_from = original.name
	if not target.file_hash:
		target.file_hash = original.file_hash
	if not target.content_hash:
		target.content_hash = original.content_hash
	if not target.ruleset_hash:
		target.ruleset_hash = original.ruleset_hash


def _resolve_original(protocol):
	"""Follow copied_from until the protocol that actually ran Schematron."""
	seen = set()
	current = protocol
	while current.copied_from and current.copied_from not in seen:
		seen.add(current.name)
		current = frappe.get_doc("E Invoice Protocol", current.copied_from)
	return current


def _ruleset_hash_for_format(einvoice_format: EInvoiceFormat, profile: str | None) -> str:
	"""Ruleset identity for reuse; empty when Schematron does not apply (non-CII)."""
	if einvoice_format != EInvoiceFormat.CII:
		return ""
	return get_ruleset_hash(profile)


def _find_original_validation(
	content_hash: str | None,
	file_hash: str | None,
	ruleset_hash: str | None,
	*,
	exclude: str | None = None,
):
	"""Find a completed protocol for these hashes + ruleset; prefer a non-copy row.

	Older settled rows without `ruleset_hash` are kept as history and are not reused
	when the current bundled stylesheets differ (or when the field was empty).
	"""
	if not content_hash or not file_hash:
		return None
	if ruleset_hash is None:
		return None

	filters = {
		"content_hash": content_hash,
		"file_hash": file_hash,
		"ruleset_hash": ruleset_hash,
		"result": ("in", list(SETTLED_RESULTS)),
	}
	if exclude:
		filters["name"] = ("!=", exclude)

	rows = frappe.get_all(
		"E Invoice Protocol",
		filters=filters,
		fields=["name", "copied_from"],
		order_by="creation asc",
		limit=20,
	)
	if not rows:
		return None
	# Prefer a row that actually ran Schematron (no copied_from)
	originals = [row for row in rows if not row.copied_from]
	name = (originals or rows)[0].name
	return _resolve_original(frappe.get_doc("E Invoice Protocol", name))


def _get_protocol_settings() -> dict:
	mode = UPLOAD_MODE_NONE
	doctypes = list(DEFAULT_PROTOCOL_DOCTYPES)
	if frappe.db.exists("DocType", "E Invoice Settings") and frappe.db.exists(
		"E Invoice Settings", "E Invoice Settings"
	):
		settings = frappe.get_cached_doc("E Invoice Settings")
		mode = settings.get("create_protocol_on_upload") or UPLOAD_MODE_NONE
		rows = settings.get("protocol_doctypes") or []
		if rows:
			doctypes = [row.document_type for row in rows if row.document_type]
	return {"create_protocol_on_upload": mode, "protocol_doctypes": set(doctypes)}


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
		root = fromstring(xml_bytes)
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


def _enqueue_for_file(method: str, file_name: str, *, job_suffix: str):
	"""Enqueue a per-File job; dedupe so workers do not pile up for the same file."""
	frappe.enqueue(
		method,
		queue="short",
		enqueue_after_commit=True,
		file_name=file_name,
		job_id=f"einvoice-protocol-{job_suffix}-{file_name}",
		deduplicate=True,
	)


def _file_lock_key(file_name: str) -> str:
	return f"einvoice-protocol-file:{file_name}"


def _hash_lock_key(content_hash: str | None, file_hash: str | None, ruleset_hash: str | None) -> str:
	return f"einvoice-protocol-hash:{content_hash}:{file_hash}:{ruleset_hash or ''}"


def _insert_protocol_stub(file_doc, xml_bytes: bytes, einvoice_format: EInvoiceFormat):
	"""Build and insert a protocol stub. Caller must hold the file advisory lock."""
	content_hash = file_doc.content_hash
	file_hash = _sha256(xml_bytes)
	profile = _cheap_profile(xml_bytes, einvoice_format)
	ruleset_hash = _ruleset_hash_for_format(einvoice_format, profile)

	doc = frappe.new_doc("E Invoice Protocol")
	doc.result = "Pending"
	doc.detection_timestamp = now_datetime()
	doc.einvoice_format = einvoice_format.value
	doc.invoice_profile = profile
	doc.file = file_doc.name
	doc.file_name = file_doc.file_name
	doc.content_hash = content_hash
	doc.file_hash = file_hash
	doc.reference_doctype = file_doc.attached_to_doctype
	doc.reference_name = file_doc.attached_to_name
	doc.company = _company_from_reference(doc.reference_doctype, doc.reference_name)

	# Same blob + XML + ruleset already settled elsewhere: copy result, skip Schematron
	source = _find_original_validation(content_hash, file_hash, ruleset_hash)
	if source:
		_apply_copied_validation(doc, source)
	elif einvoice_format != EInvoiceFormat.CII:
		# Schematron in this app is CII-oriented
		_mark_unsupported(doc, einvoice_format)

	return _insert_protocol_doc(doc)


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


def _sha256(data: bytes) -> str:
	return hashlib.sha256(data).hexdigest()
