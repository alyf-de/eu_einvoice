from __future__ import annotations

import re
from typing import TYPE_CHECKING

import frappe
from drafthorse.models.accounting import ApplicableTradeTax
from drafthorse.models.document import Document, IncludedNote
from drafthorse.models.party import TaxRegistration, URIUniversalCommunication
from drafthorse.models.payment import PaymentTerms
from drafthorse.models.trade import LogisticsServiceCharge
from drafthorse.models.tradelines import LineItem
from frappe import _
from frappe.core.utils import html2text
from frappe.utils.data import date_diff, flt, getdate, to_markdown

from eu_einvoice.common_codes import CommonCodeRetriever
from eu_einvoice.schematron import get_validation_errors
from eu_einvoice.utils import EInvoiceProfile, get_drafthorse_schema, get_guideline

if TYPE_CHECKING:
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
	from erpnext.accounts.doctype.sales_invoice_item.sales_invoice_item import SalesInvoiceItem
	from erpnext.setup.doctype.company.company import Company
	from frappe.contacts.doctype.address.address import Address
	from frappe.contacts.doctype.contact.contact import Contact

uom_codes = CommonCodeRetriever(
	["urn:xoev-de:kosit:codeliste:rec20_3", "urn:xoev-de:kosit:codeliste:rec21_3"], "C62"
)
payment_means_codes = CommonCodeRetriever(["urn:xoev-de:xrechnung:codeliste:untdid.4461_3"], "ZZZ")
duty_tax_fee_category_codes = CommonCodeRetriever(["urn:xoev-de:kosit:codeliste:untdid.5305_3"], "S")
vat_exemption_reason_codes = CommonCodeRetriever(["urn:xoev-de:kosit:codeliste:vatex_1"], "vatex-eu-ae")


@frappe.whitelist()
def download_xrechnung(invoice_id: str):
	frappe.local.response.filename = f"{invoice_id}.xml"
	frappe.local.response.filecontent = get_einvoice(invoice_id)
	frappe.local.response.type = "download"


def get_einvoice(invoice: str | SalesInvoice) -> bytes:
	if isinstance(invoice, str):
		invoice = frappe.get_doc("Sales Invoice", invoice)

	invoice.check_permission("read")
	invoice.run_method("before_einvoice_generation")

	seller_address = None
	if invoice.company_address:
		seller_address = frappe.get_doc("Address", invoice.company_address)

	buyer_address = None
	if invoice.customer_address:
		buyer_address = frappe.get_doc("Address", invoice.customer_address)

	seller_contact = None
	if invoice.get("company_contact_person"):
		seller_contact = frappe.get_doc("Contact", invoice.company_contact_person)

	buyer_contact = None
	if invoice.contact_person:
		buyer_contact = frappe.get_doc("Contact", invoice.contact_person)

	company = frappe.get_doc("Company", invoice.company)

	profile = EInvoiceProfile(invoice.einvoice_profile)
	generator = EInvoiceGenerator(
		profile=profile,
		invoice=invoice,
		company=company,
		seller_address=seller_address,
		buyer_address=buyer_address,
		seller_contact=seller_contact,
		buyer_contact=buyer_contact,
	)
	generator.create_einvoice()
	doc = generator.get_einvoice()

	invoice.run_method("after_einvoice_generation", doc)

	return doc.serialize(schema=get_drafthorse_schema(profile))


class EInvoiceGenerator:
	"""Map ERPNext entities to a Drafthorse document."""

	def __init__(
		self,
		profile: EInvoiceProfile,
		invoice: SalesInvoice,
		company: Company,
		seller_address: Address | None = None,
		buyer_address: Address | None = None,
		seller_contact: Contact | None = None,
		buyer_contact: Contact | None = None,
	):
		self.profile = profile
		self.invoice = invoice
		self.company = company
		self.seller_address = seller_address
		self.buyer_address = buyer_address
		self.seller_contact = seller_contact
		self.buyer_contact = buyer_contact
		self.doc = None
		self.item_tax_rates = set()

	def get_einvoice(self) -> Document | None:
		"""Return the einvoice document as a Python object."""
		return self.doc

	def create_einvoice(self):
		"""Create the einvoice document as a Python object."""
		self.doc = Document()

		self._set_context()
		self._set_header()
		self._set_seller()
		self._set_buyer()

		if self.invoice.buyer_reference:
			self.doc.trade.agreement.buyer_reference = self.invoice.buyer_reference

		if self.invoice.po_no:
			self.doc.trade.agreement.buyer_order.issuer_assigned_id = self.invoice.po_no

		if self.invoice.po_date:
			self.doc.trade.agreement.buyer_order.issue_date_time = getdate(self.invoice.po_date)

		for item in self.invoice.items:
			self._add_line_item(item)

		tax_added = self._add_taxes_and_charges()
		if not tax_added:
			self._add_empty_tax()

		self.doc.trade.settlement.currency_code = self.invoice.currency
		self._add_payment_means()

		if self.invoice.from_date:
			self.doc.trade.settlement.period.start = getdate(self.invoice.from_date)

		if self.invoice.to_date:
			self.doc.trade.settlement.period.end = getdate(self.invoice.to_date)

		self._add_payment_terms()
		self._set_totals()

	def _set_context(self):
		"""Set default context according to XRechnung 3.0.2"""
		self.doc.context.business_parameter.id = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
		self.doc.context.guideline_parameter.id = get_guideline(self.profile)

	def _set_header(self):
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
			note.content.add(to_markdown(self.invoice.terms).strip())
			self.doc.header.notes.add(note)

		if self.invoice.incoterm:
			note = IncludedNote(subject_code="AAR")  # Terms of delivery
			note.content.add(f"{self.invoice.incoterm} {self.invoice.named_place or ''}".strip())
			self.doc.header.notes.add(note)

	def _set_seller(self):
		self.doc.trade.agreement.seller.name = self.invoice.company
		self._set_seller_tax_id()

		if self.profile > EInvoiceProfile.BASIC:
			self._set_seller_contact()

		self._set_seller_electronic_address()
		self._set_seller_address()

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
		if self.seller_contact and self.seller_contact.email_id:
			electronic_address = self.seller_contact.email_id
		else:
			electronic_address = self.company.email

		if electronic_address:
			self.doc.trade.agreement.seller.electronic_address.add(
				URIUniversalCommunication(uri_ID=("EM", electronic_address))
			)

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

		if seller_contact_phone:
			self.doc.trade.agreement.seller.contact.telephone.number = seller_contact_phone

		if self.company.fax:
			self.doc.trade.agreement.seller.contact.fax.number = self.company.fax

	def _set_buyer(self):
		if frappe.db.get_single_value("Selling Settings", "cust_master_name") != "Customer Name":
			self.doc.trade.agreement.buyer.id = self.invoice.customer

		self.doc.trade.agreement.buyer.name = self.invoice.customer_name

		self._set_buyer_address()

		if self.profile > EInvoiceProfile.BASIC:
			self._set_buyer_contact()

		if self.invoice.contact_email:
			self.doc.trade.agreement.buyer.electronic_address.add(
				URIUniversalCommunication(uri_ID=("EM", self.invoice.contact_email))
			)

		self._set_buyer_tax_id()

	def _set_buyer_tax_id(self):
		if not self.invoice.tax_id:
			return

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

	def _set_buyer_contact(self):
		buyer_contact_phone = self.invoice.contact_mobile
		if self.buyer_contact:
			self.doc.trade.agreement.buyer.contact.person_name = self.buyer_contact.full_name
			if self.buyer_contact.department:
				self.doc.trade.agreement.buyer.contact.department_name = self.buyer_contact.department
			if self.buyer_contact.phone:
				buyer_contact_phone = self.buyer_contact.phone
			if self.invoice.contact_email:
				self.doc.trade.agreement.buyer.contact.email.address = self.invoice.contact_email

		if buyer_contact_phone:
			self.doc.trade.agreement.buyer.contact.telephone.number = buyer_contact_phone

	def _add_line_item(self, item: SalesInvoiceItem):
		li = LineItem()
		li.document.line_id = str(item.idx)
		li.product.name = item.item_name

		if self.profile > EInvoiceProfile.BASIC:
			li.product.seller_assigned_id = item.item_code
			li.product.buyer_assigned_id = item.customer_item_code
			li.product.description = html2text(item.description)

		li.agreement.net.amount = abs(
			flt(item.net_rate, item.precision("net_rate"))
		)  # [BR-27]-The Item net price (BT-146) shall NOT be negative.

		li.delivery.billed_quantity = (
			flt(item.qty, item.precision("qty")),
			uom_codes.get([("UOM", item.uom)]),
		)

		if item.delivery_note and self.profile >= EInvoiceProfile.EXTENDED:
			li.delivery.delivery_note.issuer_assigned_id = item.delivery_note
			li.delivery.delivery_note.issue_date_time = frappe.db.get_value(
				"Delivery Note", item.delivery_note, "posting_date"
			)

		li.settlement.trade_tax.type_code = "VAT"
		li.settlement.trade_tax.category_code = duty_tax_fee_category_codes.get(
			[
				("Item Tax Template", item.item_tax_template),
				("Account", item.income_account),
				("Tax Category", self.invoice.tax_category),
				("Sales Taxes and Charges Template", self.invoice.taxes_and_charges),
			]
		)
		if li.settlement.trade_tax.category_code._text == "AE":
			# [BR-AE-05] In an Invoice line (BG-25) where the Invoiced item VAT category code (BT-151) is "Reverse charge" the Invoiced item VAT rate (BT-152) shall be 0 (zero).
			li.settlement.trade_tax.rate_applicable_percent = 0
		else:
			item_tax_rate = get_item_rate(item.item_tax_template, self.invoice.taxes)
			self.item_tax_rates.add(item_tax_rate)
			li.settlement.trade_tax.rate_applicable_percent = item_tax_rate

		if li.settlement.trade_tax.rate_applicable_percent._value == 0:
			li.settlement.trade_tax.exemption_reason_code = vat_exemption_reason_codes.get(
				[
					("Item Tax Template", item.item_tax_template),
					("Account", item.income_account),
					("Tax Category", self.invoice.tax_category),
					("Sales Taxes and Charges Template", self.invoice.taxes_and_charges),
				]
			)

		li.settlement.monetary_summation.total_amount = item.amount
		self.doc.trade.items.add(li)

	def _add_taxes_and_charges(self):
		tax_added = False
		for i, tax in enumerate(self.invoice.taxes):
			if not tax.tax_amount:
				continue

			if tax.charge_type == "Actual":
				service_charge = LogisticsServiceCharge()
				service_charge.description = tax.description
				service_charge.applied_amount = tax.tax_amount
				self.doc.trade.settlement.service_charge.add(service_charge)
			elif tax.charge_type == "On Net Total":
				trade_tax = ApplicableTradeTax()
				trade_tax.calculated_amount = tax.tax_amount
				trade_tax.type_code = "VAT"
				trade_tax.category_code = duty_tax_fee_category_codes.get(
					[
						("Account", tax.account_head),
						("Tax Category", self.invoice.tax_category),
						("Sales Taxes and Charges Template", self.invoice.taxes_and_charges),
					]
				)
				tax_rate = tax.rate or frappe.db.get_value("Account", tax.account_head, "tax_rate") or 0
				trade_tax.rate_applicable_percent = tax_rate

				if len(self.invoice.taxes) == 1:
					# We only have one tax, so we can use the net total as basis amount
					trade_tax.basis_amount = self.invoice.net_total
					if len(self.item_tax_rates) == 1 and tax_rate == 0:
						# We only have one tax rate on the line items, but it was not specified on the tax row
						# so we use the tax rate from the line items.
						trade_tax.rate_applicable_percent = self.item_tax_rates.pop()
				elif hasattr(tax, "net_amount"):
					trade_tax.basis_amount = tax.net_amount
				elif hasattr(tax, "custom_net_amount"):
					trade_tax.basis_amount = tax.custom_net_amount
				elif tax.tax_amount and tax_rate:
					# We don't know the basis amount for this tax, so we try to calculate it
					trade_tax.basis_amount = round(tax.tax_amount / tax_rate * 100, 2)
				else:
					trade_tax.basis_amount = 0

				self.doc.trade.settlement.trade_tax.add(trade_tax)
				tax_added = True
			elif tax.charge_type == "On Previous Row Amount":
				trade_tax = ApplicableTradeTax()
				trade_tax.basis_amount = self.invoice.taxes[i - 1].tax_amount
				trade_tax.rate_applicable_percent = tax.rate
				trade_tax.calculated_amount = tax.tax_amount

				if self.invoice.taxes[i - 1].charge_type == "Actual":
					# VAT for a LogisticsServiceCharge
					trade_tax.type_code = "VAT"
				else:
					# A tax or duty applied on and in addition to existing duties and taxes.
					trade_tax.type_code = "SUR"

				trade_tax.category_code = duty_tax_fee_category_codes.get(
					[
						("Account", tax.account_head),
						("Tax Category", self.invoice.tax_category),
						("Sales Taxes and Charges Template", self.invoice.taxes_and_charges),
					]
				)
				self.doc.trade.settlement.trade_tax.add(trade_tax)
				tax_added = True
			elif tax.charge_type == "On Previous Row Total":
				trade_tax = ApplicableTradeTax()
				trade_tax.basis_amount = self.invoice.taxes[i - 1].total
				trade_tax.rate_applicable_percent = tax.rate
				trade_tax.calculated_amount = tax.tax_amount

				if self.invoice.taxes[i - 1].charge_type == "Actual":
					# VAT for a LogisticsServiceCharge
					trade_tax.type_code = "VAT"
				else:
					# A tax or duty applied on and in addition to existing duties and taxes.
					trade_tax.type_code = "SUR"

				trade_tax.category_code = duty_tax_fee_category_codes.get(
					[
						("Account", tax.account_head),
						("Tax Category", self.invoice.tax_category),
						("Sales Taxes and Charges Template", self.invoice.taxes_and_charges),
					]
				)
				self.doc.trade.settlement.trade_tax.add(trade_tax)
				tax_added = True

		return tax_added

	def _add_empty_tax(self):
		"""Add a 0% tax to the document, since it is mandatory."""
		trade_tax = ApplicableTradeTax()
		trade_tax.type_code = "VAT"  # [CII-DT-037] - TypeCode shall be 'VAT'
		trade_tax.category_code = duty_tax_fee_category_codes.get(
			[
				("Tax Category", self.invoice.tax_category),
				("Sales Taxes and Charges Template", self.invoice.taxes_and_charges),
			]
		)
		trade_tax.basis_amount = self.invoice.net_total
		trade_tax.rate_applicable_percent = 0
		trade_tax.calculated_amount = 0
		trade_tax.exemption_reason_code = vat_exemption_reason_codes.get(
			[
				("Tax Category", self.invoice.tax_category),
				("Sales Taxes and Charges Template", self.invoice.taxes_and_charges),
			]
		)
		self.doc.trade.settlement.trade_tax.add(trade_tax)

	def _add_payment_terms(self):
		for ps in self.invoice.payment_schedule:
			payment_terms = PaymentTerms()
			ps_description = ps.description or ""
			if ps.due_date:
				payment_terms.due = getdate(ps.due_date)

			if len(self.invoice.payment_schedule) > 1:
				payment_terms.partial_amount.add(
					(ps.payment_amount, None)
				)  # [CII-DT-031] - currencyID should not be present

			if ps.discount and ps.discount_date:
				if self.profile == EInvoiceProfile.EXTENDED:
					payment_terms.discount_terms.basis_date_time = getdate(ps.discount_date)
					payment_terms.discount_terms.basis_amount = ps.payment_amount
					if ps.discount_type == "Percentage":
						payment_terms.discount_terms.calculation_percent = ps.discount
					elif ps.discount_type == "Amount":
						payment_terms.discount_terms.actual_amount = ps.discount
				elif self.profile == EInvoiceProfile.XRECHNUNG:
					ps_description = ps_description.replace(
						"#", "//"
					)  # the character "#" is not allowed in the free text
					if ps.discount_type == "Percentage":
						discount_days = date_diff(ps.discount_date, self.invoice.posting_date)
						if discount_days < 0:
							basis_amount = (
								ps.payment_amount
								if round(ps.payment_amount, 2) != round(self.invoice.outstanding_amount, 2)
								else None
							)
							if ps_description:
								ps_description += "\n"
							ps_description += get_skonto_line(discount_days, ps.discount, basis_amount)
							ps_description += "\n"

			if ps_description:
				payment_terms.description = ps_description

			self.doc.trade.settlement.terms.add(payment_terms)

	def _add_payment_means(self):
		self.doc.trade.settlement.payment_means.type_code = payment_means_codes.get(
			[("Payment Terms Template", self.invoice.payment_terms_template)]
			+ [("Mode of Payment", term.mode_of_payment) for term in self.invoice.payment_schedule]
		)

		modes_of_payment = {ps.mode_of_payment for ps in self.invoice.payment_schedule if ps.mode_of_payment}
		for mode_of_payment in modes_of_payment:
			iban, bic = get_bank_details(mode_of_payment, self.invoice.company)
			if not iban:
				continue

			self.doc.trade.settlement.payment_means.payee_account.iban = iban

			if self.profile >= EInvoiceProfile.EN16931:
				self.doc.trade.settlement.payment_means.payee_account.account_name = self.invoice.company
				self.doc.trade.settlement.payment_means.payee_institution.bic = bic

			break

	def _set_totals(self):
		actual_charge_total = sum(tax.tax_amount for tax in self.invoice.taxes if tax.charge_type == "Actual")
		tax_total = sum(tax.tax_amount for tax in self.invoice.taxes if tax.charge_type != "Actual")
		self.doc.trade.settlement.monetary_summation.line_total = self.invoice.total

		if actual_charge_total:
			self.doc.trade.settlement.monetary_summation.charge_total = actual_charge_total

		if self.invoice.discount_amount:
			self.doc.trade.settlement.monetary_summation.allowance_total = self.invoice.discount_amount

		self.doc.trade.settlement.monetary_summation.tax_basis_total = (
			self.invoice.net_total + actual_charge_total
		)
		self.doc.trade.settlement.monetary_summation.tax_total = tax_total
		self.doc.trade.settlement.monetary_summation.tax_total_other_currency.add(
			(tax_total, self.invoice.currency)
		)
		self.doc.trade.settlement.monetary_summation.grand_total = self.invoice.grand_total

		if self.invoice.outstanding_amount == 0:
			self.doc.trade.settlement.monetary_summation.prepaid_total = self.invoice.grand_total
		else:
			self.doc.trade.settlement.monetary_summation.prepaid_total = self.invoice.total_advance

		self.doc.trade.settlement.monetary_summation.due_amount = self.invoice.outstanding_amount


def validate_vat_id(vat_id: str) -> tuple[str, str]:
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
	"""Validate the Sales Invoice form."""
	for tax_row in doc.taxes:
		if tax_row.charge_type == "On Item Quantity":
			frappe.msgprint(
				_("{0} row #{1}: Type '{2}' is not supported in e-invoice").format(
					_(doc.meta.get_label("taxes")), tax_row.idx, _(tax_row.charge_type)
				),
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

	validate_einvoice(doc)


def validate_einvoice(doc: SalesInvoice):
	doc.einvoice_is_correct = 0
	doc.validation_errors = ""

	try:
		xml_string = get_einvoice(doc).decode()
	except Exception:
		doc.validation_errors = _("Cannot create E Invoice.")
		return

	try:
		validation_errors = get_validation_errors(xml_string, EInvoiceProfile(doc.einvoice_profile))
	except Exception:
		doc.validation_errors = _("Cannot validate E Invoice schematron.")
		return

	if any(validation_errors):
		doc.validation_errors += "\n".join(validation_errors)
	else:
		doc.einvoice_is_correct = 1


def get_item_rate(item_tax_template: str | None, taxes: list[dict]) -> float | None:
	"""Get the tax rate for an item from the item tax template and the taxes table."""
	if item_tax_template:
		# match the accounts from the taxes table with the rate from the item tax template
		tax_template = frappe.get_doc("Item Tax Template", item_tax_template)
		applicable_accounts = [tax.account_head for tax in taxes if tax.account_head]

		for item_tax in tax_template.taxes:
			if item_tax.tax_type in applicable_accounts:
				return item_tax.tax_rate

	# if only one tax is on net total, return its rate
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


@frappe.whitelist(allow_guest=True)
def download_pdf(
	doctype: str, name: str, format=None, doc=None, no_letterhead=0, language=None, letterhead=None
):
	from frappe.utils.print_format import download_pdf as frappe_download_pdf

	# Regular Frappe PDF download
	# Sets frappe.local.response.filecontent to the PDF data
	frappe_download_pdf(doctype, name, format, doc, no_letterhead, language, letterhead)

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


def attach_xml_to_pdf(invoice_id: str, pdf_data: bytes) -> bytes:
	"""Return the PDF data with the invoice attached as XML.

	Params:
	    invoice_id: The name of the Sales Invoice.
	    pdf_data: The PDF data as bytes.
	"""
	from drafthorse.pdf import attach_xml

	level = frappe.db.get_value("Sales Invoice", invoice_id, "einvoice_profile")
	if level == "XRECHNUNG":
		# XRECHNUNG does not support embedding into PDF
		return pdf_data

	xml_bytes = get_einvoice(invoice_id)
	return attach_xml(pdf_data, xml_bytes, level)
