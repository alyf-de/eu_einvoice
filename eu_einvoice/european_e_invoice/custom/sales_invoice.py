from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

import frappe
from frappe.utils.data import getdate

# Some names are only re-exported, because other apps import them from here
from eu_einvoice.european_e_invoice.einvoice import (  # noqa: F401
	EInvoiceGenerator,
	TradeParty,
	attach_xml_on_submit,
	attach_xml_to_pdf,
	download_einvoice,
	download_pdf,
	duty_tax_fee_category_codes,
	get_einvoice,
	get_item_rate,
	get_xml_attachment_file_base_name,
	payment_means_codes,
	validate_doc,
	validate_vat_id,
	vat_exemption_reason_codes,
)
from eu_einvoice.switzerland import is_valid_swiss_vat_id, normalize_swiss_vat_id
from eu_einvoice.utils import EInvoiceProfile

if TYPE_CHECKING:
	from drafthorse.models.tradelines import LineItem
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
	from erpnext.accounts.doctype.sales_invoice_item.sales_invoice_item import SalesInvoiceItem
	from erpnext.selling.doctype.customer.customer import Customer
	from frappe.contacts.doctype.address.address import Address


@frappe.whitelist()
def download_xrechnung(invoice_id: str):
	download_einvoice("Sales Invoice", invoice_id)


class SalesInvoiceGenerator(EInvoiceGenerator):
	"""Map a Sales Invoice to a Drafthorse document. The seller is our Company, the buyer the Customer."""

	invoice: SalesInvoice
	tax_template_doctype = "Sales Taxes and Charges Template"

	@cached_property
	def customer(self) -> Customer:
		return frappe.get_doc("Customer", self.invoice.customer)

	def get_invoice_number(self) -> str:
		sales_invoice_number_field = frappe.db.get_single_value(
			"E Invoice Settings", "sales_invoice_number_field"
		)

		if sales_invoice_number_field and (invoice_number := self.invoice.get(sales_invoice_number_field)):
			return invoice_number

		return self.invoice.name

	def get_type_code(self) -> str:
		# https://unece.org/fileadmin/DAM/trade/untdid/d16b/tred/tred1001.htm
		if self.invoice.is_return:
			# -- Credit note --
			# Document/message for providing credit information to the relevant party.
			return "381"

		if self.invoice.amended_from:
			# -- Corrected invoice --
			# Commercial invoice that includes revised information differing from an
			# earlier submission of the same invoice.
			return "384"

		# -- Commercial invoice --
		# Document/message claiming payment for goods or services supplied under
		# conditions agreed between seller and buyer.
		return "380"

	def get_seller(self) -> TradeParty:
		company = frappe.get_doc("Company", self.invoice.company)
		seller = TradeParty(
			name=self.invoice.company,
			id=self._get_seller_id(),
			phone=company.phone_no,
			fax=company.fax,
		)

		if self.invoice.company_address:
			seller.address = frappe.get_doc("Address", self.invoice.company_address)

		if self.invoice.company_tax_id:
			try:
				seller.tax_registration = ("VA", validate_vat_id(self.invoice.company_tax_id.strip()))
			except ValueError:
				seller.tax_registration = ("FC", self.invoice.company_tax_id.strip())

		contact = None
		if self.invoice.get("company_contact_person"):
			contact = frappe.get_doc("Contact", self.invoice.company_contact_person)
			seller.person_name = contact.full_name
			seller.department = contact.department
			seller.email = contact.email_id
			if contact.phone:
				seller.phone = contact.phone

		if company.electronic_address_scheme and company.electronic_address:
			seller.electronic_address = (
				frappe.db.get_value("Common Code", company.electronic_address_scheme, "common_code"),
				company.electronic_address,
			)
		elif email := (contact.email_id if contact and contact.email_id else company.email):
			seller.electronic_address = ("EM", email)

		return seller

	def _get_seller_id(self) -> str | None:
		"""Return our supplier number at the customer (BT-29)."""
		supplier_numbers = getattr(self.customer, "supplier_numbers", None)
		if not supplier_numbers:
			return None
		for row in supplier_numbers:
			if row.company == self.invoice.company and row.supplier_number:
				return row.supplier_number
		return None

	def get_buyer(self) -> TradeParty:
		buyer = TradeParty(name=self.invoice.customer_name)

		if frappe.db.get_single_value("Selling Settings", "cust_master_name") != "Customer Name":
			buyer.id = self.invoice.customer

		if self.invoice.customer_address:
			buyer.address = frappe.get_doc("Address", self.invoice.customer_address)

		if self.invoice.contact_person:
			contact = frappe.get_doc("Contact", self.invoice.contact_person)
			buyer.person_name = contact.full_name
			buyer.department = contact.department
			buyer.email = contact.email_id
			buyer.phone = contact.phone or contact.mobile_no

		if self.customer.electronic_address_scheme and self.customer.electronic_address:
			buyer.electronic_address = (
				frappe.db.get_value("Common Code", self.customer.electronic_address_scheme, "common_code"),
				self.customer.electronic_address,
			)
		elif self.invoice.contact_email:
			buyer.electronic_address = ("EM", self.invoice.contact_email)
		elif buyer.address and buyer.address.email_id:
			buyer.electronic_address = ("EM", buyer.address.email_id)

		if self.invoice.tax_id:
			if is_valid_swiss_vat_id(self.invoice.tax_id):
				buyer.tax_registration = ("VA", normalize_swiss_vat_id(self.invoice.tax_id))
			else:
				try:
					buyer.tax_registration = ("VA", validate_vat_id(self.invoice.tax_id.strip()))
				except ValueError:
					buyer.tax_registration = ("FC", self.invoice.tax_id.strip())

		return buyer

	def get_ship_to(self) -> tuple[str, Address] | None:
		if not self.invoice.shipping_address_name:
			return None

		address = frappe.get_doc("Address", self.invoice.shipping_address_name)
		return address.address_title or self.invoice.customer_name, address

	def set_references(self) -> None:
		if self.invoice.buyer_reference:
			self.doc.trade.agreement.buyer_reference = self.invoice.buyer_reference

		if self.invoice.po_no:
			self.doc.trade.agreement.buyer_order.issuer_assigned_id = self.invoice.po_no

			if self.invoice.po_date and self.profile >= EInvoiceProfile.EXTENDED:
				self.doc.trade.agreement.buyer_order.issue_date_time = getdate(self.invoice.po_date)

		sales_orders = {item.sales_order for item in self.invoice.items if item.sales_order}
		if len(sales_orders) == 1 and self.profile >= EInvoiceProfile.EXTENDED:
			so_name = sales_orders.pop()
			self.doc.trade.agreement.seller_order.issuer_assigned_id = so_name
			self.doc.trade.agreement.seller_order.issue_date_time = frappe.db.get_value(
				"Sales Order", so_name, "transaction_date"
			)

	def get_line_ids(self, item: SalesInvoiceItem) -> tuple[str | None, str | None]:
		return item.item_code, item.customer_item_code

	def get_line_lookup(self, item: SalesInvoiceItem) -> list[tuple[str, str | None]]:
		return [
			("Item Tax Template", item.item_tax_template),
			("Account", item.income_account),
		]

	def set_line_delivery(self, li: LineItem, item: SalesInvoiceItem) -> None:
		if not item.delivery_note:
			return

		posting_date = frappe.db.get_value("Delivery Note", item.delivery_note, "posting_date")
		self.delivery_dates.append(posting_date)

		if self.profile >= EInvoiceProfile.EXTENDED:
			li.delivery.delivery_note.issuer_assigned_id = item.delivery_note
			li.delivery.delivery_note.issue_date_time = getdate(posting_date)

	def get_payee_account(self) -> tuple[str, str | None, str] | None:
		"""Return the company bank account behind the first Mode of Payment that has an IBAN."""
		modes_of_payment = {ps.mode_of_payment for ps in self.invoice.payment_schedule if ps.mode_of_payment}
		for mode_of_payment in modes_of_payment:
			iban, bic = get_bank_details(mode_of_payment, self.invoice.company)
			if iban:
				return iban, bic, self.invoice.company

		return None


def get_bank_details(mode_of_payment: str, company: str) -> tuple[str | None, str | None]:
	"""Get the bank details for a mode of payment."""
	empty_tuple = (None, None)
	if frappe.db.get_value("Mode of Payment", mode_of_payment, "type") != "Bank":
		return empty_tuple

	account = frappe.db.get_value(
		"Mode of Payment Account", {"parent": mode_of_payment, "company": company}, "default_account"
	)
	if not account:
		return empty_tuple

	bank_account_name = frappe.db.get_value(
		"Bank Account", {"account": account, "company": company, "is_company_account": 1, "disabled": 0}
	)
	if not bank_account_name:
		return empty_tuple

	iban, bank = frappe.db.get_value("Bank Account", bank_account_name, ["iban", "bank"])
	if not iban:
		return empty_tuple

	bic = frappe.db.get_value("Bank", bank, "swift_number") if bank else None
	return (iban, bic or None)
