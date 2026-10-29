from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

import frappe
from erpnext.accounts.doctype.bank_account.bank_account import get_party_bank_account
from frappe import _
from frappe.utils.data import flt, getdate

from eu_einvoice.european_e_invoice.einvoice import (
	EInvoiceGenerator,
	TradeParty,
	duty_tax_fee_category_codes,
	get_tax_registration,
	validate_doc,
	validate_einvoice,
)
from eu_einvoice.utils import EInvoiceProfile

if TYPE_CHECKING:
	from drafthorse.models.tradelines import LineItem
	from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice
	from erpnext.accounts.doctype.purchase_invoice_item.purchase_invoice_item import PurchaseInvoiceItem
	from erpnext.buying.doctype.supplier.supplier import Supplier
	from frappe.contacts.doctype.address.address import Address

# VAT categories where the buyer owes the VAT: reverse charge and intra-community supply
REVERSE_CHARGE_CATEGORIES = ("AE", "K")


class PurchaseInvoiceGenerator(EInvoiceGenerator):
	"""Map a self-billed Purchase Invoice ("Gutschrift") to a Drafthorse document.

	We issue the invoice in the name of the supplier, so the seller is the Supplier
	and the buyer is our Company.
	"""

	invoice: PurchaseInvoice
	tax_template_doctype = "Purchase Taxes and Charges Template"

	@cached_property
	def supplier(self) -> Supplier:
		return frappe.get_doc("Supplier", self.invoice.supplier)

	@cached_property
	def document_vat_category(self) -> str | None:
		"""Return the VAT category mapped to the invoice's Tax Category or tax template, if any."""
		return duty_tax_fee_category_codes.get_code(self._document_lookup())

	@cached_property
	def is_reverse_charge(self) -> bool:
		return self.document_vat_category in REVERSE_CHARGE_CATEGORIES

	def get_tax_rows(self) -> list:
		if self.is_reverse_charge:
			# The buyer's VAT rows net to zero, the e-invoice states 0% instead (BR-AE-08, BR-IC-08).
			# With no rows, the base class adds a single 0% breakdown over the net total.
			return []

		# "Valuation" rows only increase our stock value and are not part of the invoice total.
		# ponytail: "Deduct" rows are unsupported, `validate` warns about them.
		return [tax for tax in self.invoice.taxes if tax.category != "Valuation"]

	def get_type_code(self) -> str:
		# https://unece.org/fileadmin/DAM/trade/untdid/d16b/tred/tred1001.htm
		if self.invoice.is_return:
			# -- Self-billed invoice with negative amounts, referencing the original --
			# 389 keeps the self-billing marking, which a plain 381 credit note lacks. XRechnung warns
			# about 261 "self-billed credit note" (BR-DE-17). Peppol self-billing corrects the same way.
			return "389"

		if self.invoice.amended_from:
			# -- Corrected invoice --
			return "384"

		# -- Self-billed invoice --
		# An invoice the invoicee is producing instead of the seller.
		return "389"

	def get_seller(self) -> TradeParty:
		# We are the supplier's customer and use our supplier ID (BR-CO-26 needs BT-29, BT-30 or BT-31)
		seller = TradeParty(name=self.invoice.supplier_name, id=self.invoice.supplier)

		if self.invoice.supplier_address:
			seller.address = frappe.get_doc("Address", self.invoice.supplier_address)

		if self.invoice.tax_id:
			seller.tax_registration = get_tax_registration(self.invoice.tax_id)

		if self.invoice.contact_person:
			contact = frappe.get_doc("Contact", self.invoice.contact_person)
			seller.person_name = contact.full_name
			seller.department = contact.department
			seller.email = contact.email_id
			seller.phone = contact.phone or contact.mobile_no

		if self.supplier.electronic_address_scheme and self.supplier.electronic_address:
			seller.electronic_address = (
				frappe.db.get_value("Common Code", self.supplier.electronic_address_scheme, "common_code"),
				self.supplier.electronic_address,
			)
		elif email := (self.invoice.contact_email or seller.email or self.supplier.email_id):
			seller.electronic_address = ("EM", email)

		return seller

	def get_buyer(self) -> TradeParty:
		company = frappe.get_doc("Company", self.invoice.company)
		buyer = TradeParty(name=self.invoice.company, id=self._get_buyer_id())

		if self.invoice.billing_address:
			buyer.address = frappe.get_doc("Address", self.invoice.billing_address)

		if company.tax_id:
			buyer.tax_registration = get_tax_registration(company.tax_id)

		# ponytail: no buyer contact (BG-9), a Purchase Invoice has no company contact. Optional everywhere.
		if company.electronic_address_scheme and company.electronic_address:
			buyer.electronic_address = (
				frappe.db.get_value("Common Code", company.electronic_address_scheme, "common_code"),
				company.electronic_address,
			)
		elif company.email:
			buyer.electronic_address = ("EM", company.email)
		elif buyer.address and buyer.address.email_id:
			buyer.electronic_address = ("EM", buyer.address.email_id)

		return buyer

	def _get_buyer_id(self) -> str | None:
		"""Return our customer number at the supplier (BT-46)."""
		# `customer_numbers` exists from ERPNext v16 on
		for row in getattr(self.supplier, "customer_numbers", None) or []:
			if row.company == self.invoice.company and row.customer_number:
				return row.customer_number
		return None

	def get_ship_to(self) -> tuple[str, Address] | None:
		if not self.invoice.shipping_address:
			return None

		address = frappe.get_doc("Address", self.invoice.shipping_address)
		return address.address_title or self.invoice.company, address

	def set_references(self) -> None:
		purchase_orders = {item.purchase_order for item in self.invoice.items if item.purchase_order}
		purchase_order = purchase_orders.pop() if len(purchase_orders) == 1 else None

		# BT-10 is mandatory in XRechnung (BR-DE-15). As the buyer, we route it with our own order.
		# ponytail: derived, add a field when a buyer needs a real routing ID here.
		self.doc.trade.agreement.buyer_reference = purchase_order or self.invoice.name

		if purchase_order:
			self.doc.trade.agreement.buyer_order.issuer_assigned_id = purchase_order
			if self.profile >= EInvoiceProfile.EXTENDED:
				self.doc.trade.agreement.buyer_order.issue_date_time = getdate(
					frappe.db.get_value("Purchase Order", purchase_order, "transaction_date")
				)

		# ponytail: no seller order (BT-14) and despatch advice (BT-16), add when needed
		purchase_receipts = {item.purchase_receipt for item in self.invoice.items if item.purchase_receipt}
		if len(purchase_receipts) == 1 and self.profile >= EInvoiceProfile.EN16931:
			self.doc.trade.delivery.receiving_advice.issuer_assigned_id = purchase_receipts.pop()

	def get_line_ids(self, item: PurchaseInvoiceItem) -> tuple[str | None, str | None]:
		supplier_part_no = frappe.db.get_value(
			"Item Supplier",
			{"parent": item.item_code, "parenttype": "Item", "supplier": self.invoice.supplier},
			"supplier_part_no",
		)
		return supplier_part_no, item.item_code

	def get_line_lookup(self, item: PurchaseInvoiceItem) -> list[tuple[str, str | None]]:
		if self.is_reverse_charge:
			# The document's category applies to every line
			return []

		return [
			("Item Tax Template", item.item_tax_template),
			("Account", item.expense_account),
		]

	def set_line_delivery(self, li: LineItem, item: PurchaseInvoiceItem) -> None:
		if not item.purchase_receipt:
			return

		posting_date = frappe.db.get_value("Purchase Receipt", item.purchase_receipt, "posting_date")
		self.delivery_dates.append(posting_date)

		if self.profile >= EInvoiceProfile.EXTENDED:
			li.delivery.receiving_advice.issuer_assigned_id = item.purchase_receipt
			li.delivery.receiving_advice.issue_date_time = getdate(posting_date)

	def get_payee_account(self) -> tuple[str, str | None, str] | None:
		"""Return the supplier's default bank account. `Supplier.default_bank_account` is our own."""
		bank_account = get_party_bank_account("Supplier", self.invoice.supplier)
		if not bank_account:
			return None

		iban, bank = frappe.db.get_value("Bank Account", bank_account, ["iban", "bank"])
		if not iban:
			return None

		bic = frappe.db.get_value("Bank", bank, "swift_number") if bank else None
		return iban, bic or None, self.invoice.supplier_name

	def get_exemption_reason_text(self) -> str:
		# The global text in E Invoice Settings describes our own sales, so only the VATEX code is used.
		return ""


def validate(doc: PurchaseInvoice, event: str):
	"""Validate a self-billed Purchase Invoice. Others are left alone."""
	if not doc.einvoice_profile:
		# Clears the status left by an earlier profile
		validate_einvoice(doc)
		return

	warnings = []
	generator = PurchaseInvoiceGenerator(doc, EInvoiceProfile(doc.einvoice_profile))

	if doc.e_invoice_import:
		warnings.append(
			_(
				"This invoice is linked to the supplier's e-invoice {0}. A self-billed invoice would bill the same supply twice."
			).format(doc.e_invoice_import)
		)

	if doc.is_internal_supplier:
		warnings.append(
			_(
				"Supplier {0} is an internal supplier. Inter-company invoices are usually not self-billed."
			).format(doc.supplier)
		)

	if generator.is_reverse_charge:
		if flt(doc.grand_total, doc.precision("grand_total")) != flt(
			doc.net_total, doc.precision("net_total")
		):
			warnings.append(
				_(
					"With VAT category {0}, the e-invoice states no VAT, so the taxes and charges must add up to zero."
				).format(generator.document_vat_category)
			)
	else:
		for tax in doc.taxes:
			if tax.add_deduct_tax == "Deduct":
				warnings.append(
					_("{0} row #{1}: The e-invoice will not be valid with deducted taxes.").format(
						_(doc.meta.get_label("taxes")), tax.idx
					)
				)

	if not generator.get_payee_account():
		warnings.append(
			_(
				"Supplier {0} has no default bank account with an IBAN, so the e-invoice has no payee account."
			).format(doc.supplier)
		)

	for warning in warnings:
		frappe.msgprint(warning, alert=True, indicator="orange")

	validate_doc(doc, event)
