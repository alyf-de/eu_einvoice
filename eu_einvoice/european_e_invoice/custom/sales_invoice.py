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
from frappe.core.doctype.file.utils import find_file_by_url
from frappe.core.utils import html2text
from frappe.model.naming import parse_naming_series
from frappe.utils import cstr
from frappe.utils.data import cint, date_diff, flt, getdate, to_markdown
=======
from frappe.utils.data import getdate
>>>>>>> b4b66b4 (refactor: make e-invoice generation doctype-agnostic (#333))

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
	"""Local-only; mirrors ``get_safe_file_name`` in Frappe v16+ file utils."""
	return re.sub(r"[/\\%?#]", "_", file_name)


def _no_series_counter(_key: str, _digits: int) -> str:
	"""Disable the series counter so XML naming has no DB side effects."""
	return ""
=======
>>>>>>> b4b66b4 (refactor: make e-invoice generation doctype-agnostic (#333))
