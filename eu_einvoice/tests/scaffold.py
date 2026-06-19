# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

import frappe

COMPANY_NAME = "_Einvoice Embed Test Company"
COMPANY_ABBR = "_EET"
CUSTOMER_NAME = "_Einvoice Embed Test Customer"
ITEM_CODE = "_Einvoice Embed Test Item"
COMPANY_ADDRESS_TITLE = "_Einvoice Embed Test Company Address"
CUSTOMER_ADDRESS_TITLE = "_Einvoice Embed Test Customer Address"
COMPANY_TAX_ID = "DE123456789"
CUSTOMER_TAX_ID = "DE987654321"


def ensure_embed_test_masters() -> None:
	"""Create shared company, customer, item, and address masters for embed tests."""
	frappe.set_user("Administrator")
	ensure_embed_test_company()
	ensure_embed_test_customer()
	ensure_embed_test_item()
	ensure_embed_test_addresses()


def ensure_embed_test_company() -> str:
	"""Ensure the embed-test **Company** exists and return its name."""
	if frappe.db.exists("Company", COMPANY_NAME):
		company = frappe.get_doc("Company", COMPANY_NAME)
	else:
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": COMPANY_NAME,
				"abbr": COMPANY_ABBR,
				"country": "Germany",
				"default_currency": "EUR",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
				"tax_id": COMPANY_TAX_ID,
			}
		)
		company.insert(ignore_permissions=True)

	if company.tax_id != COMPANY_TAX_ID:
		frappe.db.set_value("Company", COMPANY_NAME, "tax_id", COMPANY_TAX_ID)

	return COMPANY_NAME


def ensure_embed_test_customer() -> str:
	"""Ensure the embed-test **Customer** exists and return its name."""
	if frappe.db.exists("Customer", CUSTOMER_NAME):
		customer = frappe.get_doc("Customer", CUSTOMER_NAME)
	else:
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": CUSTOMER_NAME,
				"customer_type": "Individual",
				"customer_group": "Individual",
				"territory": "All Territories",
				"tax_id": CUSTOMER_TAX_ID,
			}
		)
		customer.insert(ignore_permissions=True)

	if customer.tax_id != CUSTOMER_TAX_ID:
		frappe.db.set_value("Customer", CUSTOMER_NAME, "tax_id", CUSTOMER_TAX_ID)

	return CUSTOMER_NAME


def ensure_embed_test_item() -> str:
	"""Ensure the embed-test **Item** exists and return its code."""
	if frappe.db.exists("Item", ITEM_CODE):
		return ITEM_CODE

	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": ITEM_CODE,
			"item_name": ITEM_CODE,
			"item_group": "Products",
			"stock_uom": "Nos",
			"is_stock_item": 0,
			"is_sales_item": 1,
		}
	)
	item.insert(ignore_permissions=True)
	return ITEM_CODE


def ensure_embed_test_addresses() -> None:
	"""Ensure billing **Address** rows linked to the embed-test company and customer."""
	_ensure_linked_address(
		title=COMPANY_ADDRESS_TITLE,
		link_doctype="Company",
		link_name=COMPANY_NAME,
	)
	_ensure_linked_address(
		title=CUSTOMER_ADDRESS_TITLE,
		link_doctype="Customer",
		link_name=CUSTOMER_NAME,
	)


def embed_test_company_address() -> str | None:
	"""Return the embed-test company **Address** name, if it exists."""
	return frappe.db.get_value("Address", {"address_title": COMPANY_ADDRESS_TITLE}, "name")


def embed_test_customer_address() -> str | None:
	"""Return the embed-test customer **Address** name, if it exists."""
	return frappe.db.get_value("Address", {"address_title": CUSTOMER_ADDRESS_TITLE}, "name")


def _ensure_linked_address(*, title: str, link_doctype: str, link_name: str) -> str:
	"""Create or return a billing **Address** linked to *link_doctype* / *link_name*."""
	existing = frappe.db.get_value("Address", {"address_title": title}, "name")
	if existing:
		return existing

	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": title,
			"address_type": "Billing",
			"address_line1": "Test Street 1",
			"city": "Berlin",
			"country": "Germany",
			"links": [{"link_doctype": link_doctype, "link_name": link_name}],
		}
	)
	address.insert(ignore_permissions=True)
	return address.name
