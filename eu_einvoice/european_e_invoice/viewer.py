"""Desk API for KoSIT HTML e-invoice visualization."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import parse_json

from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import get_xml_bytes
from eu_einvoice.european_e_invoice.einvoice import get_einvoice, get_generator_class
from eu_einvoice.visualization import render_einvoice_html

# Bound IN-filter size for File / Protocol lookups (DoS via huge identifier lists).
_MAX_IDENTIFIERS = 200


@frappe.whitelist(methods=["GET", "POST"])
@frappe.read_only()
def files_with_protocol(identifiers: str | list) -> dict[str, str]:
	"""Return ``{file_name: result}`` for **File**s the caller may read that have a protocol.

	``identifiers`` may be File names and/or ``file_url`` values.
	Only files visible via ``frappe.get_list`` (File permissions) are resolved.
	Protocol rows are loaded via ``get_list`` (Protocol read permission).
	"""
	if isinstance(identifiers, str):
		identifiers = parse_json(identifiers) if identifiers.startswith("[") else [identifiers]
	identifiers = [value for value in (identifiers or []) if value][:_MAX_IDENTIFIERS]
	if not identifiers:
		return {}

	# Permission-aware resolution only - never frappe.db.exists / get_all on File
	file_names = set(
		frappe.get_list(
			"File",
			filters={"name": ("in", identifiers)},
			pluck="name",
			limit=len(identifiers),
		)
	)
	file_names.update(
		frappe.get_list(
			"File",
			filters={"file_url": ("in", identifiers)},
			pluck="name",
			limit=len(identifiers),
		)
	)
	if not file_names:
		return {}

	rows = frappe.get_list(
		"E Invoice Protocol",
		filters={"file": ("in", list(file_names))},
		fields=["file", "result"],
		limit=len(file_names),
	)
	return {row.file: row.result or "Pending" for row in rows if row.file}


@frappe.whitelist(methods=["GET", "POST"])
@frappe.read_only()
def render_from_document(doctype: str, name: str, lang: str | None = None) -> dict:
	"""Return HTML visualization and related protocol for a document with e-invoice XML."""
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	xml_bytes = _xml_from_document(doc)
	return {
		"html": _safe_render(xml_bytes, lang),
		"protocol": _protocol_for_document(doc),
	}


@frappe.whitelist(methods=["GET", "POST"])
@frappe.read_only()
def render_from_file(file_name: str, lang: str | None = None) -> dict:
	"""Return HTML visualization and related protocol for a **File** document."""
	file_doc = frappe.get_doc("File", file_name)
	file_doc.check_permission("read")
	xml_bytes = _xml_from_file_doc(file_doc)
	return {
		"html": _safe_render(xml_bytes, lang),
		"protocol": _protocol_for_file(file_doc.name),
	}


def _safe_render(xml_bytes: bytes, lang: str | None) -> str:
	try:
		return render_einvoice_html(xml_bytes, lang=_viewer_lang(lang))
	except ValueError:
		frappe.throw(_("Unsupported e-invoice format for visualization."))
	except Exception:
		frappe.log_error(title="E Invoice visualization")
		frappe.throw(_("Could not render e-invoice visualization. See Error Log for details."))


def _protocol_for_file(file_name: str | None) -> str | None:
	"""Return protocol name only if the caller may read **E Invoice Protocol**."""
	if not file_name:
		return None
	names = frappe.get_list(
		"E Invoice Protocol",
		filters={"file": file_name},
		pluck="name",
		limit=1,
	)
	return names[0] if names else None


def _protocol_for_document(doc) -> str | None:
	"""Resolve protocol via known Attach fields or Files attached to this document."""
	for fieldname in ("einvoice", "supplier_invoice_file"):
		file_url = doc.get(fieldname)
		if not file_url:
			continue
		# File must be readable by the caller
		file_names = frappe.get_list(
			"File",
			filters={"file_url": file_url},
			pluck="name",
			limit=1,
		)
		if not file_names:
			continue
		protocol = _protocol_for_file(file_names[0])
		if protocol:
			return protocol

	attached = frappe.get_list(
		"File",
		filters={"attached_to_doctype": doc.doctype, "attached_to_name": doc.name},
		pluck="name",
		limit=20,
	)
	if not attached:
		return None
	names = frappe.get_list(
		"E Invoice Protocol",
		filters={"file": ("in", attached)},
		pluck="name",
		order_by="creation desc",
		limit=1,
	)
	return names[0] if names else None


def _viewer_lang(lang: str | None) -> str:
	if lang in ("de", "en"):
		return lang
	user_lang = (frappe.local.lang or "en").split("-")[0].lower()
	return "de" if user_lang == "de" else "en"


def _xml_from_document(doc) -> bytes:
	"""Resolve e-invoice XML for outbound generators or inbound Attach fields."""
	if doc.doctype == "E Invoice Import" and doc.get("einvoice"):
		return get_xml_bytes(doc.einvoice)

	if doc.get("supplier_invoice_file"):
		return get_xml_bytes(doc.supplier_invoice_file)

	if get_generator_class(doc.doctype):
		return get_einvoice(doc)

	frappe.throw(_("No e-invoice XML found for {0}").format(doc.name))


def _xml_from_file_doc(file_doc) -> bytes:
	"""Read XML from a File, extracting from PDF when needed."""
	if file_doc.is_folder or file_doc.is_remote_file:
		frappe.throw(_("Cannot visualize a folder or remote file."))

	file_url = file_doc.file_url
	if not file_url:
		frappe.throw(_("File has no content."))

	# Reuse Import helper which understands .xml and ZUGFeRD/Factur-X PDF
	return get_xml_bytes(file_url)
