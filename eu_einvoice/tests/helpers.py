# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

import atexit
import base64
from contextlib import ExitStack
from unittest.mock import patch

import frappe
from frappe.utils import nowdate

from eu_einvoice.european_e_invoice.custom.sales_invoice import EInvoiceGenerator
from eu_einvoice.tests.scaffold import (
	COMPANY_NAME,
	COMPANY_TAX_ID,
	CUSTOMER_NAME,
	CUSTOMER_TAX_ID,
	ITEM_CODE,
	embed_test_company_address,
	embed_test_customer_address,
	ensure_embed_test_masters_committed,
)
from eu_einvoice.utils import EInvoiceProfile

LOCAL_ANNEX_PNG_BYTES = base64.b64decode("iVBORw0KGgoA=")

_MANAGED_SALES_INVOICES: set[str] = set()
_MANAGED_FILES: set[str] = set()
_CLEANUP_REGISTERED = False


def set_multi_attachment_embed_enabled(enabled: bool) -> None:
	"""Toggle ``multi_attachment_embed_enabled`` on **E Invoice Settings**.

	Uses ``set_single_value`` so tests do not enqueue the production auto-migration job
	from ``EInvoiceSettings.on_update``.
	"""
	from eu_einvoice.european_e_invoice.custom.sales_invoice_attachments import (
		set_embed_attachment_field_exclusivity,
	)

	frappe.db.set_single_value(
		"E Invoice Settings",
		"multi_attachment_embed_enabled",
		1 if enabled else 0,
	)
	set_embed_attachment_field_exclusivity(bool(enabled))


def assert_single_orange_message(substring: str) -> None:
	"""Assert exactly one orange ``msgprint`` in the message log contains *substring*."""
	matches = [message for message in frappe.get_message_log() if substring in message.message.lower()]
	if len(matches) != 1:
		raise AssertionError(f"expected one msgprint containing {substring!r}, got {len(matches)}")
	if matches[0].indicator != "orange":
		raise AssertionError(f"expected orange indicator, got {matches[0].indicator!r}")


def register_embed_test_cleanup() -> None:
	"""Register ``atexit`` cleanup for embed-test invoices and files (once per process)."""
	global _CLEANUP_REGISTERED
	if _CLEANUP_REGISTERED:
		return
	_CLEANUP_REGISTERED = True
	atexit.register(remove_managed_embed_test_data)


def remove_managed_embed_test_data() -> None:
	"""Delete embed-test **Sales Invoice** and **File** rows registered during the test run."""
	if not getattr(frappe.local, "db", None):
		return

	frappe.set_user("Administrator")
	for sales_invoice_name in sorted(_MANAGED_SALES_INVOICES, reverse=True):
		delete_embed_test_sales_invoice(sales_invoice_name)
	for file_name in sorted(_MANAGED_FILES, reverse=True):
		delete_embed_test_annex_file(file_name)
	frappe.db.commit()


def ensure_embed_test_sales_invoice() -> frappe.Document:
	"""Return a disposable draft **Sales Invoice** valid for ``create_einvoice`` (EN 16931)."""
	register_embed_test_cleanup()
	frappe.set_user("Administrator")
	ensure_embed_test_masters_committed()

	company = COMPANY_NAME
	sales_invoice = frappe.new_doc("Sales Invoice")
	sales_invoice.company = company
	sales_invoice.customer = CUSTOMER_NAME
	sales_invoice.company_address = embed_test_company_address()
	sales_invoice.customer_address = embed_test_customer_address()
	sales_invoice.posting_date = nowdate()
	sales_invoice.due_date = nowdate()
	sales_invoice.currency = frappe.db.get_value("Company", company, "default_currency")
	sales_invoice.debit_to = frappe.get_cached_value("Company", company, "default_receivable_account")
	sales_invoice.einvoice_profile = "EN 16931"
	sales_invoice.company_tax_id = COMPANY_TAX_ID
	sales_invoice.tax_id = CUSTOMER_TAX_ID
	sales_invoice.append(
		"items",
		{
			"item_code": ITEM_CODE,
			"qty": 1,
			"rate": 100,
			"income_account": frappe.get_cached_value("Company", company, "default_income_account"),
		},
	)
	sales_invoice.flags.ignore_permissions = True
	sales_invoice.insert()
	_MANAGED_SALES_INVOICES.add(sales_invoice.name)
	return sales_invoice


def create_embed_test_annex_file(
	*, file_name: str, content: bytes = LOCAL_ANNEX_PNG_BYTES
) -> frappe.Document:
	"""Create a disposable **File** row for embed attachment tests."""
	register_embed_test_cleanup()
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"is_private": 0,
			"content": content,
		}
	)
	file.save(ignore_permissions=True)
	_MANAGED_FILES.add(file.name)
	return file


def attach_embed_test_annex_file_to_sales_invoice(
	annex_file: frappe.Document, sales_invoice: frappe.Document
) -> None:
	"""Link a test annex **File** to *sales_invoice* for table-embed validation."""
	annex_file.attached_to_doctype = sales_invoice.doctype
	annex_file.attached_to_name = sales_invoice.name
	annex_file.save(ignore_permissions=True)


def delete_embed_test_annex_file(file_name: str) -> None:
	"""Delete a test **File** row and drop it from managed cleanup tracking."""
	_MANAGED_FILES.discard(file_name)
	if frappe.db.exists("File", file_name):
		frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)


def create_embed_test_sales_invoice() -> tuple[frappe.Document, frappe.Document]:
	"""Return a draft **Sales Invoice** with ``einvoice_embedded_document`` set to a test annex."""
	annex_file_name = f"legacy-create-einvoice-annex-{frappe.generate_hash(length=8)}.png"
	# Unique bytes avoid Frappe content-hash collisions with leftover public files.
	annex_content = LOCAL_ANNEX_PNG_BYTES + frappe.generate_hash(length=8).encode()
	annex_file = create_embed_test_annex_file(file_name=annex_file_name, content=annex_content)

	sales_invoice = ensure_embed_test_sales_invoice()
	annex_file.attached_to_doctype = "Sales Invoice"
	annex_file.attached_to_name = sales_invoice.name
	annex_file.attached_to_field = "einvoice_embedded_document"
	annex_file.save(ignore_permissions=True)
	sales_invoice.einvoice_embedded_document = annex_file.file_url
	sales_invoice.save(ignore_permissions=True)
	return sales_invoice, annex_file


def delete_embed_test_sales_invoice(sales_invoice_name: str) -> None:
	"""Cancel (if submitted) and delete a test **Sales Invoice**."""
	_MANAGED_SALES_INVOICES.discard(sales_invoice_name)
	if frappe.db.exists("Sales Invoice", sales_invoice_name):
		doc = frappe.get_doc("Sales Invoice", sales_invoice_name)
		if doc.docstatus == 1:
			with ExitStack() as stack:
				stack.enter_context(patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_doc"))
				if "pdf_on_submit" in frappe.get_installed_apps():
					stack.enter_context(patch("pdf_on_submit.attach_pdf.execute"))
				doc.cancel()
		frappe.delete_doc("Sales Invoice", sales_invoice_name, force=True, ignore_permissions=True)


def build_einvoice_generator(invoice) -> EInvoiceGenerator:
	"""Build an ``EInvoiceGenerator`` with addresses and contacts loaded from *invoice*."""
	seller_address = None
	if invoice.company_address:
		seller_address = frappe.get_doc("Address", invoice.company_address)

	buyer_address = None
	if invoice.customer_address:
		buyer_address = frappe.get_doc("Address", invoice.customer_address)

	shipping_address = None
	if invoice.shipping_address_name:
		shipping_address = frappe.get_doc("Address", invoice.shipping_address_name)

	seller_contact = None
	if invoice.get("company_contact_person"):
		seller_contact = frappe.get_doc("Contact", invoice.company_contact_person)

	buyer_contact = None
	if invoice.contact_person:
		buyer_contact = frappe.get_doc("Contact", invoice.contact_person)

	return EInvoiceGenerator(
		profile=EInvoiceProfile(invoice.einvoice_profile),
		invoice=invoice,
		company=frappe.get_doc("Company", invoice.company),
		customer=frappe.get_doc("Customer", invoice.customer),
		seller_address=seller_address,
		buyer_address=buyer_address,
		shipping_address=shipping_address,
		seller_contact=seller_contact,
		buyer_contact=buyer_contact,
	)
