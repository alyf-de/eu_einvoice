# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

import atexit
import base64
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
)
from eu_einvoice.utils import EInvoiceProfile

LOCAL_ANNEX_PNG_BYTES = base64.b64decode("iVBORw0KGgoA=")

_MANAGED_SALES_INVOICES: set[str] = set()
_MANAGED_FILES: set[str] = set()
_CLEANUP_REGISTERED = False


def register_embed_test_cleanup() -> None:
	global _CLEANUP_REGISTERED
	if _CLEANUP_REGISTERED:
		return
	_CLEANUP_REGISTERED = True
	atexit.register(remove_managed_embed_test_data)


def remove_managed_embed_test_data() -> None:
	if not getattr(frappe.local, "db", None):
		return

	frappe.set_user("Administrator")
	for sales_invoice_name in sorted(_MANAGED_SALES_INVOICES, reverse=True):
		delete_embed_test_sales_invoice(sales_invoice_name)
	for file_name in sorted(_MANAGED_FILES, reverse=True):
		delete_embed_test_annex_file(file_name)
	frappe.db.commit()


def ensure_embed_test_sales_invoice() -> frappe.Document:
	"""Disposable draft **Sales Invoice** valid for `create_einvoice` (EN 16931)."""
	register_embed_test_cleanup()
	frappe.set_user("Administrator")

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


def delete_embed_test_annex_file(file_name: str) -> None:
	_MANAGED_FILES.discard(file_name)
	if frappe.db.exists("File", file_name):
		frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)


def create_embed_test_sales_invoice() -> tuple[frappe.Document, frappe.Document]:
	"""Disposable draft **Sales Invoice** with a legacy embed annex file."""
	annex_file_name = f"legacy-create-einvoice-annex-{frappe.generate_hash(length=8)}.png"
	annex_file = create_embed_test_annex_file(file_name=annex_file_name)

	sales_invoice = ensure_embed_test_sales_invoice()
	sales_invoice.einvoice_embedded_document = annex_file.file_url
	sales_invoice.save(ignore_permissions=True)
	return sales_invoice, annex_file


def delete_embed_test_sales_invoice(sales_invoice_name: str) -> None:
	_MANAGED_SALES_INVOICES.discard(sales_invoice_name)
	if frappe.db.exists("Sales Invoice", sales_invoice_name):
		doc = frappe.get_doc("Sales Invoice", sales_invoice_name)
		if doc.docstatus == 1:
			with patch("eu_einvoice.european_e_invoice.custom.sales_invoice.validate_doc"):
				doc.cancel()
		frappe.delete_doc("Sales Invoice", sales_invoice_name, force=True, ignore_permissions=True)


def build_einvoice_generator(invoice) -> EInvoiceGenerator:
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
