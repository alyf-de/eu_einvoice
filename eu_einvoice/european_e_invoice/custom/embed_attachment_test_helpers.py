# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

import frappe
import yaml
from drafthorse.models.document import Document
from lxml import etree

from eu_einvoice.european_e_invoice.custom.sales_invoice import EInvoiceGenerator, as_base_64
from eu_einvoice.utils import EInvoiceProfile

_SCENARIOS_PATH = Path(__file__).with_name("embed_attachment_scenarios.yaml")


@dataclass(frozen=True)
class MockFileSpec:
	name: str
	file_url: str
	is_remote: bool
	content: bytes = b""


@dataclass(frozen=True)
class EmbedAttachmentExpectations:
	reference_count: int
	type_code: str | None = None
	issuer_assigned_id: str | None = None
	has_attached_object: bool | None = None
	filename: str | None = None
	mime_type: str | None = None
	uri_id: str | None = None


@dataclass(frozen=True)
class EmbedAttachmentScenario:
	id: str
	field_url: str
	expect: EmbedAttachmentExpectations
	mock_file: MockFileSpec | None = None


def load_embed_attachment_scenarios() -> list[EmbedAttachmentScenario]:
	"""Load YAML-driven ``_embed_attachments`` unit-test scenarios from disk."""
	raw = yaml.safe_load(_SCENARIOS_PATH.read_text(encoding="utf-8"))
	scenarios: list[EmbedAttachmentScenario] = []

	for row in raw:
		mock_row = row.get("mock_file")
		mock_file = None
		if mock_row:
			content = b""
			if content_b64 := mock_row.get("content_bytes_b64"):
				content = base64.b64decode(content_b64)
			mock_file = MockFileSpec(
				name=mock_row["name"],
				file_url=mock_row["file_url"],
				is_remote=bool(mock_row["is_remote"]),
				content=content,
			)

		expect_row = row["expect"]
		scenarios.append(
			EmbedAttachmentScenario(
				id=row["id"],
				field_url=row["field_url"],
				mock_file=mock_file,
				expect=EmbedAttachmentExpectations(
					reference_count=expect_row["reference_count"],
					type_code=expect_row.get("type_code"),
					issuer_assigned_id=expect_row.get("issuer_assigned_id"),
					has_attached_object=expect_row.get("has_attached_object"),
					filename=expect_row.get("filename"),
					mime_type=expect_row.get("mime_type"),
					uri_id=expect_row.get("uri_id"),
				),
			)
		)

	return scenarios


def make_sales_invoice_doc(**kwargs) -> frappe._dict:
	"""Return a minimal in-memory **Sales Invoice** dict for unit tests."""
	return frappe._dict(
		{
			"doctype": "Sales Invoice",
			"einvoice_embedded_document": "",
			"items": [],
			**kwargs,
		}
	)


def make_embed_generator(invoice) -> EInvoiceGenerator:
	"""Return an ``EInvoiceGenerator`` with an empty Drafthorse document for unit tests."""
	generator = EInvoiceGenerator(
		profile=EInvoiceProfile.EN16931,
		invoice=invoice,
		company=frappe._dict(name="Test Co"),
		customer=frappe._dict(name="Test Customer", supplier_numbers=[]),
	)
	generator.doc = Document()
	return generator


def mock_file_doc(spec: MockFileSpec) -> frappe._dict:
	"""Return a mock **File**-shaped dict matching *spec*."""
	return frappe._dict(
		name=spec.name,
		file_url=spec.file_url,
		is_remote_file=spec.is_remote,
		get_content=lambda *args, content=spec.content, **kwargs: content,
	)


def _element_text(value) -> str:
	"""Return the text content of a Drafthorse XML element wrapper."""
	if hasattr(value, "_text"):
		return value._text
	return str(value)


def assert_embed_attachment_result(
	generator: EInvoiceGenerator,
	expect: EmbedAttachmentExpectations,
	*,
	mock_content: bytes | None = None,
) -> None:
	"""Assert ARD 916 nodes on *generator* match *expect*."""
	refs = generator.doc.trade.agreement.additional_references.children

	if len(refs) != expect.reference_count:
		raise AssertionError(f"expected {expect.reference_count} ARD nodes, got {len(refs)}")

	if expect.reference_count == 0:
		return

	ref = refs[0]

	if expect.type_code and str(ref.type_code) != expect.type_code:
		raise AssertionError(f"expected TypeCode {expect.type_code!r}, got {ref.type_code!r}")

	if expect.issuer_assigned_id and _element_text(ref.issuer_assigned_id) != expect.issuer_assigned_id:
		raise AssertionError(
			f"expected IssuerAssignedID {expect.issuer_assigned_id!r}, got {ref.issuer_assigned_id!r}"
		)

	attached_object = ref.attached_object
	attached_content = getattr(attached_object, "_text", None) if attached_object else None

	if expect.has_attached_object is True:
		if not attached_content:
			raise AssertionError("expected attached_object payload on ARD 916")
		if expect.mime_type and attached_object._mime_code != expect.mime_type:
			raise AssertionError(f"expected MIME {expect.mime_type!r}, got {attached_object._mime_code!r}")
		if expect.filename and attached_object._filename != expect.filename:
			raise AssertionError(f"expected filename {expect.filename!r}, got {attached_object._filename!r}")
		if mock_content is not None and attached_content != as_base_64(mock_content):
			raise AssertionError("expected base64 content from mock file bytes")

	if expect.has_attached_object is False and attached_content:
		raise AssertionError("expected no attached_object payload on ARD 916")

	if expect.uri_id and _element_text(ref.uri_id) != expect.uri_id:
		raise AssertionError(f"expected URIID {expect.uri_id!r}, got {ref.uri_id!r}")


_CII_ATTACHMENT_XPATH = "//ram:AttachmentBinaryObject"
_CII_NS = {"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"}


def make_minimal_pdf_bytes() -> bytes:
	"""Return a tiny valid PDF for hybrid attach_xml_to_pdf tests."""
	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=72, height=72)
	buffer = io.BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


def extract_attachment_binary_objects_from_cii_xml(
	xml_bytes: bytes,
) -> list[tuple[str, str, str]]:
	"""Return ``(filename, mime_code, base64_payload)`` for each CII annex."""
	root = etree.fromstring(xml_bytes)
	attachments: list[tuple[str, str, str]] = []

	for element in root.xpath(_CII_ATTACHMENT_XPATH, namespaces=_CII_NS):
		attachments.append((element.get("filename") or "", element.get("mimeCode") or "", element.text or ""))

	return attachments
