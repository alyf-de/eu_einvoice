from __future__ import annotations

import mimetypes
import os
import re
from base64 import b64encode
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import frappe
from drafthorse.models.accounting import ApplicableTradeTax, AppliedTradeTax
from drafthorse.models.document import Document, IncludedNote
from drafthorse.models.party import TaxRegistration, URIUniversalCommunication
from drafthorse.models.payment import PaymentMeans, PaymentTerms
from drafthorse.models.references import AdditionalReferencedDocument
from drafthorse.models.trade import LogisticsServiceCharge
from drafthorse.models.tradelines import LineItem
from frappe import _
from frappe.core.doctype.file.utils import find_file_by_url
from frappe.core.utils import html2text
from frappe.model.naming import parse_naming_series
from frappe.utils import cstr
from frappe.utils.data import cint, date_diff, flt, getdate, to_markdown

from eu_einvoice.common_codes import CommonCodeRetriever
from eu_einvoice.schematron import get_validation_errors
from eu_einvoice.utils import EInvoiceProfile, get_drafthorse_schema, get_guideline

if TYPE_CHECKING:
	from frappe.contacts.doctype.address.address import Address
	from frappe.model.document import Document as FrappeDocument

	from eu_einvoice.european_e_invoice.doctype.e_invoice_settings.e_invoice_settings import EInvoiceSettings

uom_codes = CommonCodeRetriever(
	[
		"urn:xoev-de:kosit:codeliste:rec20",
		"urn:xoev-de:kosit:codeliste:rec21",
		"urn:cef.eu:names:identifier:Unit",
	],
	"C62",
)
payment_means_codes = CommonCodeRetriever(
	["urn:xoev-de:xrechnung:codeliste:untdid.4461", "urn:cef.eu:names:identifier:Payment"], "ZZZ"
)
duty_tax_fee_category_codes = CommonCodeRetriever(
	["urn:xoev-de:kosit:codeliste:untdid.5305", "urn:cef.eu:names:identifier:5305"], "S"
)
vat_exemption_reason_codes = CommonCodeRetriever(
	["urn:xoev-de:kosit:codeliste:vatex", "urn:cef.eu:names:identifier:VATEX"], "vatex-eu-ae"
)
# VAT categories that need an exemption reason on the VAT breakdown (BR-AE-10, BR-E-10, ...)
EXEMPT_VAT_CATEGORIES = ("AE", "E", "G", "K", "O")


@frappe.whitelist()
def download_einvoice(doctype: str, name: str):
	"""Download the e-invoice XML of a document whose doctype has a registered generator."""
	invoice = frappe.get_doc(doctype, name)
	frappe.local.response.filecontent = get_einvoice(invoice)
	frappe.local.response.filename = f"{get_xml_attachment_file_base_name(invoice)}.xml"
	frappe.local.response.type = "download"


def get_generator_class(doctype: str) -> type[EInvoiceGenerator] | None:
	"""Return the generator class registered for `doctype`, if any.

	Apps register a generator per doctype in their hooks.py, e.g.
	`einvoice_generators = {"My Invoice": "my_app.einvoice.MyInvoiceGenerator"}`.
	If several apps register the same doctype, the last installed app wins.
	"""
	paths = frappe.get_hooks("einvoice_generators", {}).get(doctype)
	return frappe.get_attr(paths[-1]) if paths else None


def get_einvoice(invoice: str | FrappeDocument, doctype: str = "Sales Invoice") -> bytes:
	if isinstance(invoice, str):
		invoice = frappe.get_doc(doctype, invoice)

	invoice.check_permission("read")
	invoice.run_method("before_einvoice_generation")

	generator_class = get_generator_class(invoice.doctype)
	if not generator_class:
		frappe.throw(_("E-invoices are not supported for {0}").format(_(invoice.doctype)))

	profile = EInvoiceProfile(invoice.einvoice_profile)
	generator = generator_class(invoice, profile)
	generator.create_einvoice()
	doc = generator.get_einvoice()

	invoice.run_method("after_einvoice_generation", doc)

	return doc.serialize(schema=get_drafthorse_schema(profile))


@dataclass
class TradeParty:
	"""Seller (BG-4) or buyer (BG-7) of an e-invoice, with the final values to write."""

	name: str
	id: str | None = None
	tax_registration: tuple[str, str] | None = None  # (scheme, ID), scheme is "VA" or "FC"
	address: Address | None = None
	person_name: str | None = None
	department: str | None = None
	email: str | None = None
	phone: str | None = None
	fax: str | None = None
	electronic_address: tuple[str, str] | None = None  # (scheme, address), e.g. ("EM", "bill@example.com")


class EInvoiceGenerator:
	"""Map an invoice-like document to a Drafthorse document.

	Subclasses provide the doctype-specific data by overriding the public methods,
	and are registered per doctype via the `einvoice_generators` hook.
	"""

	tax_template_doctype: str  # e.g. "Sales Taxes and Charges Template"

	def __init__(self, invoice: FrappeDocument, profile: EInvoiceProfile):
		self.invoice = invoice
		self.profile = profile
		self.doc = None
		self.item_tax_rates = set()
		# VAT category code -> exemption reason code, collected from the line items
		self.line_exemption_reason_codes = {}
		# item id() -> VAT category, shared by BR-O-02 and line export
		self._line_vat_category_codes = None
		self.delivery_dates = []
		self.vat_exemption_reason_text = ""

	def get_seller(self) -> TradeParty:
		"""Return the seller (BG-4)."""
		raise NotImplementedError

	def get_buyer(self) -> TradeParty:
		"""Return the buyer (BG-7)."""
		raise NotImplementedError

	def get_type_code(self) -> str:
		"""Return the invoice type code (BT-3) according to UNTDID 1001."""
		raise NotImplementedError

	def get_invoice_number(self) -> str:
		"""Return the invoice number (BT-1)."""
		return self.invoice.name

	def get_ship_to(self) -> tuple[str, Address] | None:
		"""Return name and address of the deliver-to party (BG-13), if any."""
		return None

	def set_references(self) -> None:
		"""Set document-level references, e.g. buyer reference (BT-10) and orders (BT-13, BT-14)."""

	def get_line_ids(self, item) -> tuple[str | None, str | None]:
		"""Return the seller's (BT-155) and the buyer's (BT-156) identifier of a line's item."""
		raise NotImplementedError

	def get_line_lookup(self, item) -> list[tuple[str, str | None]]:
		"""Return the line's own records (e.g. Item Tax Template and Account), most specific first.

		They are used to look up the line's VAT category and exemption reason.
		The document-level records from `_document_lookup` are appended for the VAT category.
		"""
		raise NotImplementedError

	def set_line_delivery(self, li: LineItem, item) -> None:
		"""Set the delivery references of a line and add its delivery date to `self.delivery_dates`."""

	def get_tax_rows(self) -> list:
		"""Return the tax rows to declare in the e-invoice."""
		return self.invoice.taxes

	def get_payee_account(self) -> tuple[str, str | None, str] | None:
		"""Return IBAN, BIC and account name of the payee (BG-17), if known."""
		return None

	def get_exemption_reason_text(self) -> str:
		"""Return the optional VAT exemption reason text (BT-120)."""
		return str(
			frappe.db.get_single_value("E Invoice Settings", "vat_exemption_reason_text") or ""
		).strip()

	def get_einvoice(self) -> Document | None:
		"""Return the einvoice document as a Python object."""
		return self.doc

	def create_einvoice(self):
		"""Create the einvoice document as a Python object."""
		self.doc = Document()
		self.vat_exemption_reason_text = self.get_exemption_reason_text()

		self._set_context()
		self._set_header()
		self._write_party(self.doc.trade.agreement.seller, self.get_seller())
		self._write_party(self.doc.trade.agreement.buyer, self.get_buyer())
		self._set_ship_to()
		self.set_references()

		if self.profile >= EInvoiceProfile.EN16931:
			self._embed_attachment()

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

		self._add_delivery_date()
		self._add_payment_terms()
		self._set_totals()

	def _embed_attachment(self):
		"""Add the embedded document to the einvoice."""
		if not self.invoice.einvoice_embedded_document:
			return

		file = find_file_by_url(self.invoice.einvoice_embedded_document)

		content = None
		if not file.is_remote_file:
			file_name = os.path.basename(file.file_url)
			mime_type = mimetypes.guess_type(file.file_url)[0]
			content = as_base_64(file.get_content())

		ref_doc = AdditionalReferencedDocument()
		ref_doc.issuer_assigned_id = file.name
		if file.is_remote_file:
			ref_doc.uri_id = file.file_url
		else:
			ref_doc.attached_object = (mime_type, file_name, content)
		ref_doc.type_code = "916"  # "Related document" according to UNTDID 1001
		self.doc.trade.agreement.additional_references.add(ref_doc)

	def _document_lookup(self) -> list[tuple[str, str | None]]:
		"""Return the document-level records to look up VAT categories and exemption reasons for."""
		return [
			("Tax Category", self.invoice.tax_category),
			(self.tax_template_doctype, self.invoice.taxes_and_charges),
		]

	def _set_context(self):
		"""Set default context according to XRechnung 3.0.2"""
		self.doc.context.business_parameter.id = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
		self.doc.context.guideline_parameter.id = get_guideline(self.profile)

	def _set_header(self):
		self.doc.header.id = self.get_invoice_number()
		self.doc.header.type_code = self.get_type_code()

		if self.invoice.is_return:
			self._set_referenced_invoice(self.invoice.return_against)
		elif self.invoice.amended_from:
			self._set_referenced_invoice(self.invoice.amended_from)

		self.doc.header.issue_date_time = getdate(self.invoice.posting_date)

		if self.invoice.terms:
			note = IncludedNote(subject_code="ABC")  # Conditions of sale or purchase
			note.content = to_markdown(self.invoice.terms).strip()
			self.doc.header.notes.add(note)

		if self.invoice.incoterm:
			note = IncludedNote(subject_code="AAR")  # Terms of delivery
			note.content = f"{self.invoice.incoterm} {self.invoice.named_place or ''}".strip()
			self.doc.header.notes.add(note)

	def _set_referenced_invoice(self, invoice_name: str | None):
		"""Set the preceding invoice reference (BG-3) of a credit note or corrected invoice."""
		self.doc.trade.settlement.invoice_referenced_document.issuer_assigned_id = invoice_name
		self.doc.trade.settlement.invoice_referenced_document.issue_date_time = frappe.db.get_value(
			self.invoice.doctype, invoice_name, "posting_date"
		)

	def _write_party(self, target, party: TradeParty):
		"""Write a seller or buyer to the Drafthorse trade party `target`."""
		target.name = party.name

		if party.id:
			target.id = party.id

		if party.tax_registration:
			# BR-O-02 forbids seller and buyer VAT IDs on category O invoices, but allows FC.
			if party.tax_registration[0] != "VA" or not self._has_not_subject_to_vat_line():
				target.tax_registrations.add(TaxRegistration(id=party.tax_registration))

		if party.address:
			self._write_address(target.address, party.address)

		if party.electronic_address:
			target.electronic_address.uri_ID = party.electronic_address

		if self.profile == EInvoiceProfile.BASIC:
			return

		if party.person_name:
			target.contact.person_name = party.person_name
		if party.department:
			target.contact.department_name = party.department
		if party.email:
			target.contact.email.address = party.email
		if party.phone and self.profile >= EInvoiceProfile.EN16931:
			target.contact.telephone.number = party.phone
		if party.fax and self.profile >= EInvoiceProfile.EXTENDED:
			target.contact.fax.number = party.fax

	def _write_address(self, target, address: Address):
		target.line_one = address.address_line1
		target.line_two = address.address_line2
		target.postcode = address.pincode
		target.city_name = address.city
		target.country_id = frappe.db.get_value("Country", address.country, "code").upper()

	def _set_ship_to(self):
		ship_to = self.get_ship_to()
		if not ship_to:
			return

		name, address = ship_to
		self.doc.trade.delivery.ship_to.name = name
		self._write_address(self.doc.trade.delivery.ship_to.address, address)

	def _resolve_line_vat_category_codes(self) -> dict[int, str | None]:
		"""Resolve each line's VAT category once for BR-O-02 and line export."""
		if self._line_vat_category_codes is None:
			self._line_vat_category_codes = {
				id(item): duty_tax_fee_category_codes.get(
					self.get_line_lookup(item) + self._document_lookup()
				)
				for item in self.invoice.items
			}
		return self._line_vat_category_codes

	def _has_not_subject_to_vat_line(self) -> bool:
		"""Return whether any invoice line uses VAT category O."""
		return "O" in self._resolve_line_vat_category_codes().values()

	def _add_line_item(self, item):
		li = LineItem()
		li.document.line_id = str(item.idx)
		li.product.name = item.item_name

		if self.profile > EInvoiceProfile.BASIC:
			li.product.seller_assigned_id, li.product.buyer_assigned_id = self.get_line_ids(item)
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

		self.set_line_delivery(li, item)

		li.settlement.trade_tax.type_code = "VAT"
		line_lookup = self.get_line_lookup(item)
		category_code = self._resolve_line_vat_category_codes()[id(item)]
		li.settlement.trade_tax.category_code = category_code
		if category_code in EXEMPT_VAT_CATEGORIES:
			# Only the line knows Item Tax Template and Account; the rest is looked up again
			# on the VAT breakdown. ponytail: first line wins, one breakdown holds only one reason.
			exemption_reason_code = vat_exemption_reason_codes.get_code(line_lookup)
			if exemption_reason_code:
				self.line_exemption_reason_codes.setdefault(category_code, exemption_reason_code)

		if category_code in ("AE", "E", "G", "K", "Z"):
			# BR-AE-05, BR-E-05, BR-G-05, BR-IC-05, BR-Z-05 (rate 0.00; see EN 16931 tax code guidance)
			li.settlement.trade_tax.rate_applicable_percent = 0
		elif category_code != "O":
			item_tax_rate = get_item_rate(item.item_tax_template, self.get_tax_rows())
			self.item_tax_rates.add(item_tax_rate)
			li.settlement.trade_tax.rate_applicable_percent = item_tax_rate
		# else BR-O-05: category O must not contain BT-152 (omit rate entirely, not even 0)
		# BT-120/BT-121 (exemption reason) belong only on the document-level VAT breakdown (BG-23).

		li.settlement.monetary_summation.total_amount = flt(item.net_amount, item.precision("net_amount"))
		self.doc.trade.items.add(li)

	def _add_taxes_and_charges(self):
		taxes = self.get_tax_rows()
		tax_added = False
		for i, tax in enumerate(taxes):
			lookup = [("Account", tax.account_head), *self._document_lookup()]
			if tax.charge_type == "Actual" and self.profile >= EInvoiceProfile.EXTENDED:
				service_charge = LogisticsServiceCharge()
				service_charge.description = tax.description
				service_charge.applied_amount = tax.tax_amount

				if len(taxes) > i + 1:
					vat_line = taxes[i + 1]
					if vat_line.charge_type in ("On Previous Row Amount", "On Previous Row Total"):
						# Add applied VAT for the service charge (BR-FXEXT-S-08)
						service_charge_tax = AppliedTradeTax()
						service_charge_tax.type_code = "VAT"
						service_charge_tax.category_code = duty_tax_fee_category_codes.get(
							[("Account", vat_line.account_head), *self._document_lookup()]
						)
						if service_charge_tax.category_code._text != "O":
							# BR-O-07: category O must not contain a VAT rate
							service_charge_tax.rate_applicable_percent = vat_line.rate
						service_charge.trade_tax.add(service_charge_tax)

				self.doc.trade.settlement.service_charge.add(service_charge)
			elif tax.charge_type == "On Net Total":
				tax_rate = tax.rate or frappe.db.get_value("Account", tax.account_head, "tax_rate") or 0
				if tax.tax_amount == 0 and tax_rate != 0 and tax_rate not in self.item_tax_rates:
					# No line item uses this rate, so there is nothing to declare for it.
					# True 0% rows are kept, as are rates used by a line with a net amount of 0.
					continue
				trade_tax = ApplicableTradeTax()
				trade_tax.calculated_amount = tax.tax_amount
				trade_tax.type_code = "VAT"
				trade_tax.category_code = duty_tax_fee_category_codes.get(lookup)

				self._set_header_vat_category_rate(trade_tax, tax_rate)

				if len(taxes) == 1:
					# We only have one tax, so we can use the net total as basis amount
					trade_tax.basis_amount = self.invoice.net_total
					if (
						trade_tax.category_code._text != "O"
						and len(self.item_tax_rates) == 1
						and tax_rate == 0
					):
						# We only have one tax rate on the line items, but it was not specified on the tax row
						# so we use the tax rate from the line items.
						trade_tax.rate_applicable_percent = self.item_tax_rates.pop()
				else:
					# Prefer ERPNext tax-row net_amount (frappe/erpnext#54687). hasattr is always
					# true once the field exists, so use flt() and fall back when still 0.
					basis = flt(tax.get("net_amount")) or flt(tax.get("custom_net_amount"))
					if not basis and tax.tax_amount and tax_rate:
						basis = flt(tax.tax_amount / tax_rate * 100, self.invoice.precision("net_total"))
					trade_tax.basis_amount = basis

				self._set_document_vat_exemption_reason(trade_tax, lookup)

				self.doc.trade.settlement.trade_tax.add(trade_tax)
				tax_added = True
			elif tax.charge_type == "On Previous Row Amount":
				trade_tax = ApplicableTradeTax()
				trade_tax.basis_amount = taxes[i - 1].tax_amount
				trade_tax.calculated_amount = tax.tax_amount

				if taxes[i - 1].charge_type == "Actual":
					# VAT for a LogisticsServiceCharge
					trade_tax.type_code = "VAT"
				else:
					# A tax or duty applied on and in addition to existing duties and taxes.
					trade_tax.type_code = "SUR"

				trade_tax.category_code = duty_tax_fee_category_codes.get(lookup)
				self._set_header_vat_category_rate(trade_tax, tax.rate)
				self._set_document_vat_exemption_reason(trade_tax, lookup)
				self.doc.trade.settlement.trade_tax.add(trade_tax)
				tax_added = True
			elif tax.charge_type == "On Previous Row Total":
				trade_tax = ApplicableTradeTax()
				trade_tax.basis_amount = taxes[i - 1].total
				trade_tax.calculated_amount = tax.tax_amount

				if taxes[i - 1].charge_type == "Actual":
					# VAT for a LogisticsServiceCharge
					trade_tax.type_code = "VAT"
				else:
					# A tax or duty applied on and in addition to existing duties and taxes.
					trade_tax.type_code = "SUR"

				trade_tax.category_code = duty_tax_fee_category_codes.get(lookup)
				self._set_header_vat_category_rate(trade_tax, tax.rate)
				self._set_document_vat_exemption_reason(trade_tax, lookup)
				self.doc.trade.settlement.trade_tax.add(trade_tax)
				tax_added = True

		return tax_added

	def _add_empty_tax(self):
		"""Add a 0% tax to the document, since it is mandatory."""
		trade_tax = ApplicableTradeTax()
		trade_tax.type_code = "VAT"  # [CII-DT-037] - TypeCode shall be 'VAT'
		trade_tax.category_code = duty_tax_fee_category_codes.get(self._document_lookup())
		trade_tax.basis_amount = self.invoice.net_total
		self._set_header_vat_category_rate(trade_tax, 0)
		trade_tax.calculated_amount = 0
		self._set_document_vat_exemption_reason(trade_tax, self._document_lookup())
		self.doc.trade.settlement.trade_tax.add(trade_tax)

	def _set_header_vat_category_rate(self, trade_tax: ApplicableTradeTax, rate: float) -> None:
		"""Set BT-119 on the document-level VAT breakdown (BG-23).

		EN 16931 BR-48 allows omitting the rate for category O. XRechnung BR-DE-14
		requires RateApplicablePercent on every header ApplicableTradeTax, so O uses 0.00.
		"""
		if trade_tax.category_code._text != "O" or self.profile == EInvoiceProfile.XRECHNUNG:
			trade_tax.rate_applicable_percent = rate

	def _set_document_vat_exemption_reason(
		self, trade_tax: ApplicableTradeTax, lookup: list[tuple[str, str | None]]
	) -> None:
		"""Set BT-120/BT-121 on the document-level VAT breakdown (BG-23) when required.

		Per EN 16931 tax code guidance, the exemption reason is only stated for document
		level totals. Required for AE, E, G, K, O (BR-*-10); forbidden for S and Z (BR-S-10, BR-Z-10).
		"""
		category_code = trade_tax.category_code._text
		if category_code not in EXEMPT_VAT_CATEGORIES:
			return

		# Most specific first: the breakdown's own tax account, then the line items,
		# then the invoice-wide records and the default.
		tax_account_records = [record for record in lookup if record[0] == "Account"]
		exemption_reason_code = (
			vat_exemption_reason_codes.get_code(tax_account_records)
			or self.line_exemption_reason_codes.get(category_code)
			or vat_exemption_reason_codes.get(lookup)
		)
		trade_tax.exemption_reason_code = exemption_reason_code.upper()
		self._set_optional_vat_exemption_reason_text(trade_tax)

	def _set_optional_vat_exemption_reason_text(self, trade_tax: ApplicableTradeTax) -> None:
		if self.vat_exemption_reason_text:
			trade_tax.exemption_reason = self.vat_exemption_reason_text

	def _add_delivery_date(self):
		if self.delivery_dates:
			delivery_date = sorted(self.delivery_dates)[-1]
		elif self.invoice.to_date:
			delivery_date = self.invoice.to_date
		else:
			delivery_date = self.invoice.posting_date

		self.doc.trade.delivery.event.occurrence = getdate(delivery_date)

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
		# BG-16 may repeat, but we emit a single payment instruction
		payment_means = PaymentMeans()
		payment_means.type_code = payment_means_codes.get(
			[("Payment Terms Template", self.invoice.payment_terms_template)]
			+ [("Mode of Payment", term.mode_of_payment) for term in self.invoice.payment_schedule]
		)

		payee_account = self.get_payee_account()
		if payee_account:
			iban, bic, account_name = payee_account
			payment_means.payee_account.iban = iban

			if self.profile >= EInvoiceProfile.EN16931:
				payment_means.payee_account.account_name = account_name
				payment_means.payee_institution.bic = bic

		self.doc.trade.settlement.payment_means.add(payment_means)

	def _set_totals(self):
		# [BR-DEC-09]-The allowed maximum number of decimals for the Sum of Invoice line net amount (BT-106) is 2.
		self.doc.trade.settlement.monetary_summation.line_total = flt(self.invoice.net_total, 2)

		taxes = self.get_tax_rows()
		actual_charge_total = sum(tax.tax_amount for tax in taxes if tax.charge_type == "Actual")
		if actual_charge_total:
			# [BR-DEC-11]-The allowed maximum number of decimals for the Sum of charges on document level (BT-108) is 2.
			self.doc.trade.settlement.monetary_summation.charge_total = flt(actual_charge_total, 2)

		# [BR-DEC-12]-The allowed maximum number of decimals for the Invoice total amount without VAT (BT-109) is 2.
		self.doc.trade.settlement.monetary_summation.tax_basis_total = flt(
			self.invoice.net_total + actual_charge_total, 2
		)

		tax_total = sum(tax.tax_amount for tax in taxes if tax.charge_type != "Actual")
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
	"""Validate the form of an invoice that supports e-invoices."""
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

	if settings.should_validate(doc.docstatus):
		validate_einvoice(doc)

		if not doc.einvoice_is_correct and settings.should_show_message(doc.docstatus):
			frappe.msgprint(
				msg=doc.validation_errors.replace("\n", "<br><br>"),
				title=_("E Invoice is not correct"),
				raise_exception=settings.should_raise_exception(doc.docstatus),
			)


def validate_einvoice(doc: FrappeDocument):
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


def attach_xml_on_submit(doc: FrappeDocument, event: str):
	"""
	Attach XML file to the invoice on submit if auto-attach is enabled.

	This function is called via doc_events hook when an invoice is submitted.
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


def _attach_xml_file(doc: FrappeDocument, xml_content: bytes, field_name: str | None):
	"""
	Create File document and attach XML to specified field or as general attachment.

	Args:
	    doc: Invoice document
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

	# Update field value on the invoice only if field is specified
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


def as_base_64(content: str | bytes) -> str:
	"""Convert a string or bytes object to a base64-encoded string."""
	if isinstance(content, str):
		content = content.encode("utf-8")

	return b64encode(content).decode("utf-8")


# Overrides frappe.utils.print_format.download_pdf, which allows guests (shared print links)
# and checks print permission itself. The XML is only added if `get_einvoice` passes its read check.
# nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
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

	# If the doctype supports e-invoices, try to attach the XML to the PDF
	if get_generator_class(doctype):
		zugferd_pdf = None
		try:
			# If creating and attaching XML fails, we still want to return the original PDF.
			zugferd_pdf = attach_xml_to_pdf(name, frappe.local.response.filecontent, doctype)
		except Exception:
			frappe.log_error(f"Error attaching XML to PDF for {doctype} {name}")

		if zugferd_pdf:
			# If attaching XML was successful, replace the original PDF with the ZUGFeRD PDF
			frappe.local.response.filecontent = zugferd_pdf


def _get_icc_profile_path() -> str:
	"""Get the path to the ICC profile used by Ghostscript."""
	import os
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
	import os
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


def attach_xml_to_pdf(invoice_id: str, pdf_data: bytes, doctype: str = "Sales Invoice") -> bytes:
	"""Return the PDF data with the invoice attached as XML.

	Returns the PDF unchanged if `doctype` does not support e-invoices.

	Params:
	    invoice_id: The name of the invoice.
	    pdf_data: The PDF data as bytes.
	    doctype: The doctype of the invoice.
	"""
	from drafthorse.pdf import attach_xml

	if not get_generator_class(doctype):
		return pdf_data

	if _is_ghostscript_installed():
		try:
			pdf_data = _convert_pdf_to_pdfa(pdf_data)
		except RuntimeError:
			frappe.log_error("Error converting PDF to PDF/A-3 using Ghostscript.")

	level = frappe.db.get_value(doctype, invoice_id, "einvoice_profile")
	if not level or level == "XRECHNUNG":
		# XRECHNUNG does not support embedding into PDF
		return pdf_data

	xml_bytes = get_einvoice(invoice_id, doctype)
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
	"""Local-only; mirrors ``get_safe_file_name`` in Frappe v16+ file utils."""
	return re.sub(r"[/\\%?#]", "_", file_name)


def _no_series_counter(_key: str, _digits: int) -> str:
	"""Disable the series counter so XML naming has no DB side effects."""
	return ""
