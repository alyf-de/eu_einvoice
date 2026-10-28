from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

import frappe
<<<<<<< HEAD
from drafthorse.models.accounting import ApplicableTradeTax, AppliedTradeTax
from drafthorse.models.document import Document, IncludedNote
from drafthorse.models.party import TaxRegistration, URIUniversalCommunication
from drafthorse.models.payment import PaymentMeans, PaymentTerms
from drafthorse.models.references import AdditionalReferencedDocument
from drafthorse.models.trade import LogisticsServiceCharge
from drafthorse.models.tradelines import LineItem
from frappe import _
from frappe.core.utils import html2text
from frappe.model.naming import parse_naming_series
from frappe.utils import cstr
from frappe.utils.data import cint, date_diff, flt, getdate, to_markdown

from eu_einvoice.common_codes import CommonCodeRetriever
from eu_einvoice.european_e_invoice.custom.sales_invoice_attachments import (
	get_embed_attachments,
	migrate_legacy_embed_to_table,
	validate_einvoice_attachment_rows,
)
from eu_einvoice.schematron import get_validation_errors
=======
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
>>>>>>> b4b66b4 (refactor: make e-invoice generation doctype-agnostic (#333))
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

<<<<<<< HEAD
		if self.profile >= EInvoiceProfile.EN16931:
			self._embed_attachments()

		sales_orders = set()
		for item in self.invoice.items:
			if item.sales_order:
				sales_orders.add(item.sales_order)

			self._add_line_item(item)

=======
		sales_orders = {item.sales_order for item in self.invoice.items if item.sales_order}
>>>>>>> b4b66b4 (refactor: make e-invoice generation doctype-agnostic (#333))
		if len(sales_orders) == 1 and self.profile >= EInvoiceProfile.EXTENDED:
			so_name = sales_orders.pop()
			self.doc.trade.agreement.seller_order.issuer_assigned_id = so_name
			self.doc.trade.agreement.seller_order.issue_date_time = frappe.db.get_value(
				"Sales Order", so_name, "transaction_date"
			)

	def get_line_ids(self, item: SalesInvoiceItem) -> tuple[str | None, str | None]:
		return item.item_code, item.customer_item_code

<<<<<<< HEAD
		self.doc.trade.settlement.currency_code = self.invoice.currency
		self._add_payment_means()

		if self.invoice.from_date:
			self.doc.trade.settlement.period.start = getdate(self.invoice.from_date)

		if self.invoice.to_date:
			self.doc.trade.settlement.period.end = getdate(self.invoice.to_date)

		self._add_delivery_date()
		self._add_payment_terms()
		self._set_totals()

	def _embed_attachments(self):
		"""Add embedded documents to the e-invoice as CII ARD 916 references.

		Raises:
			frappe.ValidationError: When a **File** row is missing or embed filenames
				are not unique (BR-DE-22).
		"""
		attachments = get_embed_attachments(self.invoice)
		for attachment in attachments:
			file = frappe.get_doc("File", attachment.file)
			ref_doc = AdditionalReferencedDocument()
			ref_doc.issuer_assigned_id = file.name
			if file.is_remote_file:
				ref_doc.uri_id = file.file_url
			else:
				mime_type = mimetypes.guess_type(attachment.file_name)[0]
				# encodings=[] keeps binary payloads as bytes. Default File.get_content()
				# may decode small files via windows-1252 and corrupt PNG/PDF bytes.
				content = as_base_64(file.get_content(encodings=[]))
				ref_doc.attached_object = (mime_type, attachment.file_name, content)
			ref_doc.type_code = "916"  # "Related document" according to UNTDID 1001
			self.doc.trade.agreement.additional_references.add(ref_doc)

	def _set_context(self):
		"""Set default context according to XRechnung 3.0.2"""
		self.doc.context.business_parameter.id = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
		self.doc.context.guideline_parameter.id = get_guideline(self.profile)

	def _set_header(self):
		sales_invoice_number_field = frappe.db.get_single_value(
			"E Invoice Settings", "sales_invoice_number_field"
		)

		if sales_invoice_number_field and (invoice_number := self.invoice.get(sales_invoice_number_field)):
			self.doc.header.id = invoice_number
		else:
			self.doc.header.id = self.invoice.name

		# https://unece.org/fileadmin/DAM/trade/untdid/d16b/tred/tred1001.htm
		if self.invoice.is_return:
			# -- Credit note --
			# Document/message for providing credit information to the relevant party.
			self.doc.header.type_code = "381"
			self.doc.trade.settlement.invoice_referenced_document.issuer_assigned_id = (
				self.invoice.return_against
			)
			self.doc.trade.settlement.invoice_referenced_document.issue_date_time = frappe.db.get_value(
				"Sales Invoice", self.invoice.return_against, "posting_date"
			)
		elif self.invoice.amended_from:
			# -- Corrected invoice --
			# Commercial invoice that includes revised information differing from an
			# earlier submission of the same invoice.
			self.doc.header.type_code = "384"
			self.doc.trade.settlement.invoice_referenced_document.issuer_assigned_id = (
				self.invoice.amended_from
			)
			self.doc.trade.settlement.invoice_referenced_document.issue_date_time = frappe.db.get_value(
				"Sales Invoice", self.invoice.amended_from, "posting_date"
			)
		else:
			# -- Commercial invoice --
			# Document/message claiming payment for goods or services supplied under
			# conditions agreed between seller and buyer.
			self.doc.header.type_code = "380"

		self.doc.header.issue_date_time = getdate(self.invoice.posting_date)

		if self.invoice.terms:
			note = IncludedNote(subject_code="ABC")  # Conditions of sale or purchase
			note.content = to_markdown(self.invoice.terms).strip()
			self.doc.header.notes.add(note)

		if self.invoice.incoterm:
			note = IncludedNote(subject_code="AAR")  # Terms of delivery
			note.content = f"{self.invoice.incoterm} {self.invoice.named_place or ''}".strip()
			self.doc.header.notes.add(note)

	def _set_seller(self):
		self.doc.trade.agreement.seller.name = self.invoice.company
		self._set_seller_tax_id()

		if self.profile > EInvoiceProfile.BASIC:
			self._set_seller_contact()

		self._set_seller_id()
		self._set_seller_electronic_address()
		self._set_seller_address()

	def _set_seller_id(self):
		supplier_numbers = getattr(self.customer, "supplier_numbers", None)
		if not supplier_numbers:
			return
		for row in supplier_numbers:
			if row.company == self.invoice.company and row.supplier_number:
				self.doc.trade.agreement.seller.id = row.supplier_number
				break

	def _set_seller_tax_id(self):
		if not self.invoice.company_tax_id:
			return

		try:
			seller_tax_id = validate_vat_id(self.invoice.company_tax_id.strip())
			seller_vat_scheme = "VA"
		except ValueError:
			seller_tax_id = self.invoice.company_tax_id.strip()
			seller_vat_scheme = "FC"

		self.doc.trade.agreement.seller.tax_registrations.add(
			TaxRegistration(
				id=(seller_vat_scheme, seller_tax_id),
			)
		)

	def _set_seller_address(self):
		if not self.seller_address:
			return

		self.doc.trade.agreement.seller.address.line_one = self.seller_address.address_line1
		self.doc.trade.agreement.seller.address.line_two = self.seller_address.address_line2
		self.doc.trade.agreement.seller.address.postcode = self.seller_address.pincode
		self.doc.trade.agreement.seller.address.city_name = self.seller_address.city
		self.doc.trade.agreement.seller.address.country_id = frappe.db.get_value(
			"Country", self.seller_address.country, "code"
		).upper()

	def _set_seller_electronic_address(self):
		if self.company.electronic_address_scheme and self.company.electronic_address:
			self.doc.trade.agreement.seller.electronic_address.uri_ID = (
				frappe.db.get_value("Common Code", self.company.electronic_address_scheme, "common_code"),
				self.company.electronic_address,
			)
			return

		if self.seller_contact and self.seller_contact.email_id:
			electronic_address = self.seller_contact.email_id
		else:
			electronic_address = self.company.email

		if electronic_address:
			self.doc.trade.agreement.seller.electronic_address.uri_ID = ("EM", electronic_address)

	def _set_seller_contact(self):
		seller_contact_phone = self.company.phone_no
		if self.seller_contact:
			self.doc.trade.agreement.seller.contact.person_name = self.seller_contact.full_name
			if self.seller_contact.department:
				self.doc.trade.agreement.seller.contact.department_name = self.seller_contact.department
			if self.seller_contact.email_id:
				self.doc.trade.agreement.seller.contact.email.address = self.seller_contact.email_id
			if self.seller_contact.phone:
				seller_contact_phone = self.seller_contact.phone

		if seller_contact_phone and self.profile >= EInvoiceProfile.EN16931:
			self.doc.trade.agreement.seller.contact.telephone.number = seller_contact_phone

		if self.company.fax and self.profile >= EInvoiceProfile.EXTENDED:
			self.doc.trade.agreement.seller.contact.fax.number = self.company.fax

	def _set_buyer(self):
		if frappe.db.get_single_value("Selling Settings", "cust_master_name") != "Customer Name":
			self.doc.trade.agreement.buyer.id = self.invoice.customer

		self.doc.trade.agreement.buyer.name = self.invoice.customer_name

		self._set_buyer_address()
		self._set_shipping_address()

		if self.profile > EInvoiceProfile.BASIC:
			self._set_buyer_contact()

		self._set_buyer_electronic_address()
		self._set_buyer_tax_id()

	def _set_buyer_electronic_address(self):
		if self.customer.electronic_address_scheme and self.customer.electronic_address:
			self.doc.trade.agreement.buyer.electronic_address.uri_ID = (
				frappe.db.get_value("Common Code", self.customer.electronic_address_scheme, "common_code"),
				self.customer.electronic_address,
			)
			return

		if self.invoice.contact_email:
			self.doc.trade.agreement.buyer.electronic_address.uri_ID = ("EM", self.invoice.contact_email)
		elif self.buyer_address and self.buyer_address.email_id:
			self.doc.trade.agreement.buyer.electronic_address.uri_ID = ("EM", self.buyer_address.email_id)

	def _set_buyer_tax_id(self):
		if not self.invoice.tax_id:
			return

		if is_valid_swiss_vat_id(self.invoice.tax_id):
			customer_tax_id = normalize_swiss_vat_id(self.invoice.tax_id)
			customer_vat_scheme = "VA"
		else:
			try:
				customer_tax_id = validate_vat_id(self.invoice.tax_id.strip())
				customer_vat_scheme = "VA"
			except ValueError:
				customer_tax_id = self.invoice.tax_id.strip()
				customer_vat_scheme = "FC"

		self.doc.trade.agreement.buyer.tax_registrations.add(
			TaxRegistration(
				id=(customer_vat_scheme, customer_tax_id),
			)
		)

	def _set_buyer_address(self):
		if not self.buyer_address:
			return

		self.doc.trade.agreement.buyer.address.line_one = self.buyer_address.address_line1
		self.doc.trade.agreement.buyer.address.line_two = self.buyer_address.address_line2
		self.doc.trade.agreement.buyer.address.postcode = self.buyer_address.pincode
		self.doc.trade.agreement.buyer.address.city_name = self.buyer_address.city
		self.doc.trade.agreement.buyer.address.country_id = frappe.db.get_value(
			"Country", self.buyer_address.country, "code"
		).upper()

	def _set_shipping_address(self):
		if not self.shipping_address:
			return

		self.doc.trade.delivery.ship_to.name = (
			self.shipping_address.address_title or self.invoice.customer_name
		)
		self.doc.trade.delivery.ship_to.address.line_one = self.shipping_address.address_line1
		self.doc.trade.delivery.ship_to.address.line_two = self.shipping_address.address_line2
		self.doc.trade.delivery.ship_to.address.postcode = self.shipping_address.pincode
		self.doc.trade.delivery.ship_to.address.city_name = self.shipping_address.city
		self.doc.trade.delivery.ship_to.address.country_id = frappe.db.get_value(
			"Country", self.shipping_address.country, "code"
		).upper()

	def _set_buyer_contact(self):
		if self.buyer_contact:
			self.doc.trade.agreement.buyer.contact.person_name = self.buyer_contact.full_name
			if self.buyer_contact.department:
				self.doc.trade.agreement.buyer.contact.department_name = self.buyer_contact.department
			if self.buyer_contact.email_id:
				self.doc.trade.agreement.buyer.contact.email.address = self.buyer_contact.email_id

			if self.profile >= EInvoiceProfile.EN16931:
				if self.buyer_contact.phone:
					self.doc.trade.agreement.buyer.contact.telephone.number = self.buyer_contact.phone
				elif self.buyer_contact.mobile_no:
					self.doc.trade.agreement.buyer.contact.telephone.number = self.buyer_contact.mobile_no

	def _add_line_item(self, item: SalesInvoiceItem):
		li = LineItem()
		li.document.line_id = str(item.idx)
		li.product.name = item.item_name

		if self.profile > EInvoiceProfile.BASIC:
			li.product.seller_assigned_id = item.item_code
			li.product.buyer_assigned_id = item.customer_item_code
			li.product.description = html2text(item.description)

		# ERPNext won’t accept negative quantities, and the e-invoice rules (BR-27)
		# won’t accept negative prices. To work around this, we flip the signs:
		# a line that would have had a negative price and positive quantity is
		# instead sent with a positive price and a negative quantity.
		multiplier = -1 if item.net_rate < 0 and item.qty > 0 else 1

		li.agreement.net.amount = flt(item.net_rate, item.precision("net_rate")) * multiplier
		li.delivery.billed_quantity = (
			flt(item.qty, item.precision("qty")) * multiplier,
			uom_codes.get([("UOM", item.uom)]),
		)

		if item.delivery_note:
			posting_date = frappe.db.get_value("Delivery Note", item.delivery_note, "posting_date")
			self.delivery_dates.append(posting_date)

			if self.profile >= EInvoiceProfile.EXTENDED:
				li.delivery.delivery_note.issuer_assigned_id = item.delivery_note
				li.delivery.delivery_note.issue_date_time = getdate(posting_date)

		li.settlement.trade_tax.type_code = "VAT"
		lookup = [
=======
	def get_line_lookup(self, item: SalesInvoiceItem) -> list[tuple[str, str | None]]:
		return [
>>>>>>> b4b66b4 (refactor: make e-invoice generation doctype-agnostic (#333))
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

<<<<<<< HEAD
			payment_means.payee_account.iban = iban

			if self.profile >= EInvoiceProfile.EN16931:
				payment_means.payee_account.account_name = self.invoice.company
				payment_means.payee_institution.bic = bic

			break

		self.doc.trade.settlement.payment_means.add(payment_means)

	def _set_totals(self):
		# [BR-DEC-09]-The allowed maximum number of decimals for the Sum of Invoice line net amount (BT-106) is 2.
		self.doc.trade.settlement.monetary_summation.line_total = flt(self.invoice.net_total, 2)

		actual_charge_total = sum(tax.tax_amount for tax in self.invoice.taxes if tax.charge_type == "Actual")
		if actual_charge_total:
			# [BR-DEC-11]-The allowed maximum number of decimals for the Sum of charges on document level (BT-108) is 2.
			self.doc.trade.settlement.monetary_summation.charge_total = flt(actual_charge_total, 2)

		# [BR-DEC-12]-The allowed maximum number of decimals for the Invoice total amount without VAT (BT-109) is 2.
		self.doc.trade.settlement.monetary_summation.tax_basis_total = flt(
			self.invoice.net_total + actual_charge_total, 2
		)

		tax_total = sum(tax.tax_amount for tax in self.invoice.taxes if tax.charge_type != "Actual")
		# [BR-DEC-13]-The allowed maximum number of decimals for the Invoice total VAT amount (BT-110) is 2.
		self.doc.trade.settlement.monetary_summation.tax_total_other_currency.add(
			(flt(tax_total, 2), self.invoice.currency)
		)

		# [BR-DEC-14]-The allowed maximum number of decimals for the Invoice total amount with VAT (BT-112) is 2.
		self.doc.trade.settlement.monetary_summation.grand_total = flt(self.invoice.grand_total, 2)

		# [BR-DEC-16]-The allowed maximum number of decimals for the Paid amount (BT-113) is 2.
		if self.invoice.outstanding_amount == 0:
			self.doc.trade.settlement.monetary_summation.prepaid_total = flt(self.invoice.grand_total, 2)
		else:
			self.doc.trade.settlement.monetary_summation.prepaid_total = flt(self.invoice.total_advance, 2)

		# [BR-DEC-18]-The allowed maximum number of decimals for the Amount due for payment (BT-115) is 2.
		self.doc.trade.settlement.monetary_summation.due_amount = flt(self.invoice.outstanding_amount, 2)


def validate_vat_id(vat_id: str) -> str:
	COUNTRY_CODE_REGEX = r"^[A-Z]{2}$"
	VAT_NUMBER_REGEX = r"^[0-9A-Za-z\+\*\.]{2,12}$"

	country_code = vat_id[:2].upper()
	vat_number = vat_id[2:].replace(" ", "")

	# check vat_number and country_code with regex
	if not re.match(COUNTRY_CODE_REGEX, country_code):
		raise ValueError("Invalid country code")

	if not re.match(VAT_NUMBER_REGEX, vat_number):
		raise ValueError("Invalid VAT number")

	return country_code + vat_number


def validate_doc(doc, event):
	"""Validate **Sales Invoice** e-invoice fields on save and submit.

	When ``multi_attachment_embed_enabled`` is on, migrates ``einvoice_embedded_document``
	to ``einvoice_attachments`` and validates child-table rows (unique filenames,
	duplicate-content warning) before e-invoice validation.

	Args:
		doc (SalesInvoice): The invoice being validated.
		event (str): Frappe document event name (for example ``"validate"``).
	"""
	for tax_row in doc.taxes:
		if tax_row.charge_type == "On Item Quantity":
			frappe.msgprint(
				_("{0} row #{1}: Type '{2}' is not supported in e-invoice").format(
					_(doc.meta.get_label("taxes")), tax_row.idx, _(tax_row.charge_type)
				),
				alert=True,
				indicator="orange",
			)

		if (
			tax_row.charge_type == "Actual"
			and EInvoiceProfile(doc.einvoice_profile) < EInvoiceProfile.EXTENDED
		):
			frappe.msgprint(
				_(
					"{0} row #{1}: The charge type 'Actual' is only supported in the eInvoice profiles 'EXTENDED' and 'XRECHNUNG'."
				).format(_(doc.meta.get_label("taxes")), tax_row.idx),
				alert=True,
				indicator="orange",
			)

	modes_of_payment = set()
	for ps in doc.payment_schedule:
		if ps.discount_date and date_diff(ps.discount_date, doc.posting_date) < 0:
			frappe.msgprint(
				_("{0} row #{1}: Discount Date should be after Posting Date").format(
					_(doc.meta.get_label("payment_schedule")), ps.idx
				),
				alert=True,
				indicator="orange",
			)

		if ps.mode_of_payment:
			modes_of_payment.add(ps.mode_of_payment)

	if len(modes_of_payment) > 1:
		frappe.msgprint(
			_("{0}: Only one mode of payment will be considered in the e-invoice.").format(
				_(doc.meta.get_label("payment_schedule"))
			),
			alert=True,
			indicator="orange",
		)

	if doc.discount_amount:
		frappe.msgprint(
			_("A document level discount is currently not supported in the e-invoice."),
			alert=True,
			indicator="orange",
		)

	settings: EInvoiceSettings = frappe.get_single("E Invoice Settings")

	if settings.multi_attachment_embed_enabled:
		migrate_legacy_embed_to_table(doc)

		if doc.get("einvoice_attachments"):
			validate_einvoice_attachment_rows(doc, settings)

	if settings.should_validate(doc.docstatus):
		validate_einvoice(doc)

		if not doc.einvoice_is_correct and settings.should_show_message(doc.docstatus):
			frappe.msgprint(
				msg=doc.validation_errors.replace("\n", "<br><br>"),
				title=_("E Invoice is not correct"),
				raise_exception=settings.should_raise_exception(doc.docstatus),
			)


def validate_einvoice(doc: SalesInvoice):
	doc.einvoice_is_correct = 0
	doc.validation_errors = ""
	doc.validation_warnings = ""

	if not doc.einvoice_profile:
		return

	try:
		xml_bytes = get_einvoice(doc)
		doc._einvoice_xml_bytes = xml_bytes
		xml_string = xml_bytes.decode()
	except Exception:
		msg = _("Cannot create E Invoice.")
		doc.validation_errors = msg
		frappe.log_error(msg, reference_doctype=doc.doctype, reference_name=doc.name)
		return

	try:
		invoice_profile = EInvoiceProfile(doc.einvoice_profile)
		validation_errors, warnings = get_validation_errors(xml_string, invoice_profile)

		if invoice_profile == EInvoiceProfile.XRECHNUNG:
			basic_errors, basic_warnings = get_validation_errors(xml_string, EInvoiceProfile.EN16931)
			validation_errors += basic_errors
			warnings += basic_warnings
	except Exception:
		msg = _("Cannot validate E Invoice schematron.")
		doc.validation_errors = msg
		frappe.log_error(msg, reference_doctype=doc.doctype, reference_name=doc.name)
		return

	if any(validation_errors):
		doc.validation_errors += "\n".join(validation_errors)
	else:
		doc.einvoice_is_correct = 1

	if any(warnings):
		doc.validation_warnings += "\n".join(warnings)


def attach_xml_on_submit(doc: SalesInvoice, event: str):
	"""
	Attach XML file to Sales Invoice on submit if auto-attach is enabled.

	This function is called via doc_events hook when a Sales Invoice is submitted.
	It checks E Invoice Settings and attaches the XRECHNUNG XML to the specified field
	or as a general attachment if no field is specified.
	"""
	if not doc.einvoice_profile or doc.einvoice_profile != "XRECHNUNG":
		return

	settings = frappe.get_cached_doc("E Invoice Settings")
	if not settings.auto_attach_xml:
		return

	try:
		xml_content = getattr(doc, "_einvoice_xml_bytes", None) or get_einvoice(doc)
	except Exception:
		doc.log_error("E Invoice Auto-Attach Failed")
		# Don't raise exception - allow submit to continue even if attachment fails
		return

	# Pass None if no field is specified to create general attachment
	field_name = settings.attach_field_for_xml_file or None
	try:
		_attach_xml_file(doc, xml_content, field_name)
	except Exception:
		doc.log_error("E Invoice Auto-Attach File Creation Failed")
		# Don't raise exception - allow submit to continue
		return


def _attach_xml_file(doc: SalesInvoice, xml_content: bytes, field_name: str | None):
	"""
	Create File document and attach XML to specified field or as general attachment.

	Args:
	    doc: Sales Invoice document
	    xml_content: XML file content as bytes
	    field_name: Target attachment field name (None for general attachment)
	"""
	if not xml_content:
		doc.log_error("E Invoice Auto-Attach: Empty XML content")
		return

	if field_name:
		if not hasattr(doc, field_name):
			doc.log_error(
				title="E Invoice Auto-Attach: invalid field",
				message=f"Field '{field_name}' is configured for XML attachment, but does not exist on the document.",
			)
			return

		if doc.get(field_name):
			doc.log_error(
				title="E Invoice Auto-Attach: conflicting value",
				message=f"Field '{field_name}' is configured for XML attachment, but already has a value.",
			)
			return

	base_name = get_xml_attachment_file_base_name(doc)
	file_name = f"{base_name}.xml"

	# Create new File document
	file_doc = frappe.new_doc("File")
	file_doc.file_name = file_name
	file_doc.content = xml_content
	file_doc.folder = "Home/Attachments"
	file_doc.is_private = 1
	file_doc.attached_to_doctype = doc.doctype
	file_doc.attached_to_name = doc.name

	# Only set attached_to_field if field is specified
	if field_name:
		file_doc.attached_to_field = field_name

	# Save file
	file_doc.save(ignore_permissions=True)

	# Update field value on Sales Invoice only if field is specified
	if field_name:
		doc.db_set(field_name, file_doc.file_url)


def get_item_rate(item_tax_template: str | None, taxes: list) -> float | None:
	"""Resolve the VAT % for this line from the Item Tax Template and the invoice tax rows.

	1) Return the ``tax_rate`` of the first template row (in template order) whose ``tax_type``
	   matches one of the invoice's ``account_head`` values. When *Not Applicable*
	   (`not_applicable`) exists on **Item Tax Template Detail** (ERPNext v16 / v15 via
	   frappe/erpnext#54687), skip those rows and allow a genuine 0% rate. Otherwise only
	   non-zero ``tax_rate`` rows are considered (legacy templates that list unused taxes at 0%).
	2) If every matching row was skipped, the template states that none of the invoice's taxes
	   apply to this line, so the rate is 0.
	3) If no template row matched at all: if there is exactly one *On Net Total* row, use its
	   ``rate``.
	"""
	if item_tax_template:
		tax_template = frappe.get_cached_doc("Item Tax Template", item_tax_template)
		applicable_accounts = [tax.account_head for tax in taxes if tax.account_head]
		has_not_applicable = bool(frappe.get_meta("Item Tax Template Detail").get_field("not_applicable"))
		matched_but_skipped = False

		def _is_applicable(item_tax) -> bool:
			if has_not_applicable:
				return not cint(item_tax.get("not_applicable"))
			# Legacy templates list unused taxes at 0%, so a 0% row cannot be told apart
			# from a genuine 0% rate.
			return bool(item_tax.tax_rate)

		for item_tax in tax_template.taxes:
			if item_tax.tax_type not in applicable_accounts:
				continue
			if _is_applicable(item_tax):
				return item_tax.tax_rate
			matched_but_skipped = True

		if matched_but_skipped:
			return 0.0

	tax_rates = [invoice_tax.rate for invoice_tax in taxes if invoice_tax.charge_type == "On Net Total"]
	return tax_rates[0] if len(tax_rates) == 1 else None


def get_skonto_line(days: int, percent: float, basis_amount: float | None = None):
	"""Return a string containing codified early payment discount terms.

	According to the document [Angabe von Skonto bei der Nutzung des Übertragungskanals
	„Upload“ an den Rechnungseingangsplattformen des Bundes](https://www.e-rechnung-bund.de/wp-content/uploads/2023/04/Angabe-Skonto-Upload.pdf)
	"""
	parts = [
		"SKONTO",
		f"TAGE={days}",
		f"PROZENT={percent:.2f}",
	]

	if basis_amount:
		parts.append(f"BASISBETRAG={basis_amount:.2f}")

	return "#" + "#".join(parts) + "#"
=======
		return None
>>>>>>> b4b66b4 (refactor: make e-invoice generation doctype-agnostic (#333))


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
<<<<<<< HEAD


def as_base_64(content: str | bytes) -> str:
	"""Convert a string or bytes object to a base64-encoded string."""
	if isinstance(content, str):
		content = content.encode("utf-8")

	return b64encode(content).decode("utf-8")


@frappe.whitelist(allow_guest=True)
def download_pdf(
	doctype: str,
	name: str,
	format: str | None = None,
	doc: FrappeDocument | str | dict[str, Any] | None = None,
	no_letterhead: str | int | None = None,
	language: str | None = None,
	letterhead: str | None = None,
	pdf_generator: Literal["wkhtmltopdf", "chrome"] | None = None,
):
	from frappe.utils.print_format import download_pdf as frappe_download_pdf

	# Regular Frappe PDF download
	# Sets frappe.local.response.filecontent to the PDF data
	frappe_download_pdf(doctype, name, format, doc, no_letterhead, language, letterhead, pdf_generator)

	# If the doctype is a Sales Invoice, try to attach the XML to the PDF
	if doctype == "Sales Invoice":
		zugferd_pdf = None
		try:
			# If creating and attaching XML fails, we still want to return the original PDF.
			zugferd_pdf = attach_xml_to_pdf(name, frappe.local.response.filecontent)
		except Exception:
			frappe.log_error(f"Error attaching XML to PDF for Sales Invoice {name}")

		if zugferd_pdf:
			# If attaching XML was successful, replace the original PDF with the ZUGFeRD PDF
			frappe.local.response.filecontent = zugferd_pdf


def _get_icc_profile_path() -> str:
	"""Get the path to the ICC profile used by Ghostscript."""
	import re
	import subprocess

	gs_output = subprocess.run(["gs", "-h"], capture_output=True, text=True)
	# the output of `gs -h` contains the search paths for Ghostscript
	# it looks like the following:
	# ...
	# Search path:
	#   /usr/share/ghostscript/9.55.0/Resource/Init :
	#   /usr/share/ghostscript/9.55.0/lib :
	#   /usr/share/ghostscript/9.55.0/Resource/Font :
	#   /usr/share/ghostscript/fonts : /var/lib/ghostscript/fonts :
	#   /usr/share/cups/fonts : /usr/share/ghostscript/fonts :
	#   /usr/local/lib/ghostscript/fonts : /usr/share/fonts
	# Ghostscript is also using fontconfig to search for font files
	# ...
	search_paths = re.search(
		r"Search path:([\s\S]+) (\/.+\/lib) ([\s\S]+)Ghostscript", gs_output.stdout, re.DOTALL
	)
	if not search_paths:
		raise RuntimeError("Unable to find /lib path in Ghostscript search paths")

	library_path = search_paths.group(2).strip()
	icc_path = os.path.join(library_path[:-4], "iccprofiles")

	if not os.path.exists(icc_path):
		raise RuntimeError("Unable to find ICC profiles folder in Ghostscript search paths.")

	return icc_path


def _convert_pdf_to_pdfa(pdf_data: bytes) -> bytes:
	"""Convert the PDF data to PDF/A-3 using Ghostscript."""
	import subprocess

	cwd = None
	if not os.path.isfile("srgb.icc"):
		# the PDFA_def.ps file requires the srgb.icc file to be present in the current directory
		# if it is not present, change the current working directory to the icc profile path.
		cwd = _get_icc_profile_path()

	with subprocess.Popen(
		[
			"gs",
			"-q",
			"-sstdout=%stderr",
			"-dPDFA=3",
			"-dBATCH",
			"-dNOPAUSE",
			"-dPDFACompatibilityPolicy=2",
			"-sColorConversionStrategy=RGB",
			"--permit-file-read=srgb.icc",
			"-sDEVICE=pdfwrite",
			"-sOutputFile=-",
			"PDFA_def.ps",
			"-",
		],
		cwd=cwd,
		stdin=subprocess.PIPE,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
	) as proc:
		pdfa_data, err = proc.communicate(input=pdf_data)
		if proc.returncode != 0:
			raise RuntimeError(f"Ghostscript error: {err.decode()}")
		return pdfa_data


def _is_ghostscript_installed() -> bool:
	"""Check if Ghostscript is installed on the system."""
	import shutil

	return shutil.which("gs") is not None


def attach_xml_to_pdf(invoice_id: str, pdf_data: bytes) -> bytes:
	"""Return the PDF data with the invoice attached as XML.

	Params:
	    invoice_id: The name of the Sales Invoice.
	    pdf_data: The PDF data as bytes.
	"""
	from drafthorse.pdf import attach_xml

	if _is_ghostscript_installed():
		try:
			pdf_data = _convert_pdf_to_pdfa(pdf_data)
		except RuntimeError:
			frappe.log_error("Error converting PDF to PDF/A-3 using Ghostscript.")

	level = frappe.db.get_value("Sales Invoice", invoice_id, "einvoice_profile")
	if not level or level == "XRECHNUNG":
		# XRECHNUNG does not support embedding into PDF
		return pdf_data

	xml_bytes = get_einvoice(invoice_id)
	return attach_xml(pdf_data, xml_bytes, level)


def get_xml_attachment_file_base_name(doc, *, pattern: str | None = None) -> str:
	"""Filename stem (no `.xml`) for the downloadable XML.

	Uses *pattern* when given, otherwise reads *Auto name format for XML file*
	from **E Invoice Settings**. Falls back to `doc.name` when the pattern is
	empty or fails to resolve. Result is sanitized via `_get_safe_file_name`
	(same rules as **File** attachments).
	"""
	if pattern is None:
		pattern = frappe.get_single_value("E Invoice Settings", "auto_name_format_for_xml_file")
	pattern = cstr(pattern).strip()
	if pattern:
		try:
			base = parse_naming_series(pattern, doc=doc, number_generator=_no_series_counter).strip()
		except Exception:
			frappe.log_error(
				title=_("E Invoice XML file name pattern failed"),
				message=frappe.get_traceback(),
				reference_doctype=doc.doctype,
				reference_name=doc.name,
			)
			base = ""
		if base:
			return _get_safe_file_name(base)
	return _get_safe_file_name(doc.name)


def _get_safe_file_name(file_name: str) -> str:
	"""Local-only; mirrors ``get_safe_file_name`` in Frappe v17+ file utils."""
	return re.sub(r"[/\\%?#]", "_", file_name)


def _no_series_counter(_key: str, _digits: int) -> str:
	"""Disable the series counter so XML naming has no DB side effects."""
	return ""
=======
>>>>>>> b4b66b4 (refactor: make e-invoice generation doctype-agnostic (#333))
