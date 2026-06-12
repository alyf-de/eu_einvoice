# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

import frappe
from drafthorse.models.document import Document

from eu_einvoice.european_e_invoice.custom.legacy_embed_scenarios import (
	EmbedAttachmentExpectations,
	MockFileSpec,
)
from eu_einvoice.european_e_invoice.custom.sales_invoice import EInvoiceGenerator, as_base_64
from eu_einvoice.utils import EInvoiceProfile


def make_sales_invoice_doc(**kwargs) -> frappe._dict:
	return frappe._dict(
		{
			"doctype": "Sales Invoice",
			"einvoice_embedded_document": "",
			**kwargs,
		}
	)


def make_embed_generator(invoice) -> EInvoiceGenerator:
	generator = EInvoiceGenerator(
		profile=EInvoiceProfile.EN16931,
		invoice=invoice,
		company=frappe._dict(name="Test Co"),
		customer=frappe._dict(name="Test Customer", supplier_numbers=[]),
	)
	generator.doc = Document()
	return generator


def mock_file_doc(spec: MockFileSpec) -> frappe._dict:
	return frappe._dict(
		name=spec.name,
		file_url=spec.file_url,
		is_remote_file=spec.is_remote,
		get_content=lambda content=spec.content: content,
	)


def _element_text(value) -> str:
	if hasattr(value, "_text"):
		return value._text
	return str(value)


def assert_embed_attachment_result(
	generator: EInvoiceGenerator,
	expect: EmbedAttachmentExpectations,
	*,
	mock_content: bytes | None = None,
) -> None:
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
