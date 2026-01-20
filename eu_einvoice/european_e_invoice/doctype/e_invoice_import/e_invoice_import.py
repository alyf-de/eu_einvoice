# Copyright (c) 2024, ALYF GmbH and contributors
# For license information, please see license.txt


from pathlib import Path
from typing import TYPE_CHECKING

import frappe
from drafthorse.models.document import Document as DrafthorseDocument
from erpnext import get_default_company
from erpnext.edi.doctype.code_list.code_list import get_docnames_for
from facturx import get_xml_from_pdf
from frappe import _, _dict, get_site_path
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from lxml.etree import XMLSyntaxError

from eu_einvoice.schematron import get_validation_errors
from eu_einvoice.utils import EInvoiceProfile, get_profile

if TYPE_CHECKING:
	from drafthorse.models.accounting import ApplicableTradeTax, MonetarySummation
	from drafthorse.models.party import PostalTradeAddress, TradeParty
	from drafthorse.models.payment import PaymentTerms
	from drafthorse.models.trade import BillingSpecifiedPeriod, PaymentMeans
	from drafthorse.models.tradelines import LineItem
	from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice


class EInvoiceImport(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from eu_einvoice.european_e_invoice.doctype.e_invoice_item.e_invoice_item import EInvoiceItem
		from eu_einvoice.european_e_invoice.doctype.e_invoice_payment_term.e_invoice_payment_term import (
			EInvoicePaymentTerm,
		)
		from eu_einvoice.european_e_invoice.doctype.e_invoice_trade_tax.e_invoice_trade_tax import (
			EInvoiceTradeTax,
		)

		allowance_total: DF.Currency
		amended_from: DF.Link | None
		billing_period_end: DF.Date | None
		billing_period_start: DF.Date | None
		buyer_address_line_1: DF.Data | None
		buyer_address_line_2: DF.Data | None
		buyer_city: DF.Data | None
		buyer_country: DF.Link | None
		buyer_electronic_address: DF.Data | None
		buyer_electronic_address_scheme: DF.Data | None
		buyer_name: DF.Data | None
		buyer_postcode: DF.Data | None
		charge_total: DF.Currency
		company: DF.Link | None
		currency: DF.Link | None
		due_date: DF.Date | None
		due_payable: DF.Currency
		e_invoice_is_correct: DF.Check
		einvoice: DF.Attach | None
		grand_total: DF.Currency
		id: DF.Data | None
		issue_date: DF.Date | None
		items: DF.Table[EInvoiceItem]
		line_total: DF.Currency
		payee_account_name: DF.Data | None
		payee_bic: DF.Data | None
		payee_iban: DF.Data | None
		payment_terms: DF.Table[EInvoicePaymentTerm]
		profile: DF.ReadOnly | None
		purchase_order: DF.Link | None
		seller_address_line_1: DF.Data | None
		seller_address_line_2: DF.Data | None
		seller_city: DF.Data | None
		seller_country: DF.Link | None
		seller_electronic_address: DF.Data | None
		seller_electronic_address_scheme: DF.Data | None
		seller_name: DF.Data | None
		seller_postcode: DF.Data | None
		seller_tax_id: DF.Data | None
		supplier: DF.Link | None
		supplier_address: DF.Link | None
		tax_basis_total: DF.Currency
		tax_total: DF.Currency
		taxes: DF.Table[EInvoiceTradeTax]
		total_prepaid: DF.Currency
		validation_errors: DF.Text | None
		validation_warnings: DF.Text | None
	# end: auto-generated types

	def validate(self):
		if (
			self.id
			and self.supplier
			and frappe.db.get_single_value("Accounts Settings", "check_supplier_invoice_uniqueness")
			and frappe.db.exists(
				"E Invoice Import", {"id": self.id, "name": ("!=", self.name), "supplier": self.supplier}
			)
		):
			frappe.throw(_("An E Invoice Import with the same Invoice ID and Supplier already exists."))

	def before_save(self):
		if self.einvoice and self.has_value_changed("einvoice"):
			self.read_values_from_einvoice()
			self.guess_supplier()
			self.guess_company()
			self.guess_uom()
			self.guess_item_code()

		self.guess_po_details()

	def before_submit(self):
		if not self.supplier:
			frappe.throw(_("Please create or select a supplier before submitting"))

		if not self.company:
			frappe.throw(_("Please select a company before submitting"))

		if not (self.items and all(row.item for row in self.items)):
			frappe.throw(_("Please map all invoice lines to an item before submitting"))

	def on_submit(self):
		self.add_seller_product_ids_to_items()

	def onload(self):
		if self.docstatus == 0:
			return

		invoices = frappe.get_list(
			"Purchase Invoice",
			filters={
				"bill_no": self.id,
				"supplier": self.supplier,
				"company": self.company,
				"docstatus": ("!=", 2),
			},
			fields=["name", "e_invoice_import"],
		)
		linked_invoice = next(
			(invoice.name for invoice in invoices if invoice.e_invoice_import == self.name), None
		)
		unlinked_invoice = next((invoice.name for invoice in invoices if not invoice.e_invoice_import), None)

		self.set_onload("linked_invoice", linked_invoice)
		self.set_onload("unlinked_invoice", unlinked_invoice)

	def get_xml_bytes(self) -> bytes:
		return get_xml_bytes(self.einvoice)

	def read_values_from_einvoice(self) -> None:
		xml_bytes = self.get_xml_bytes()
		try:
			doc = DrafthorseDocument.parse(xml_bytes, strict=False)
		except XMLSyntaxError:
			frappe.throw(_("The uploaded file does not contain valid XML data."))

		self.profile = get_profile(doc.context.guideline_parameter.id._text).value
		self._validate_schematron(xml_bytes)

		self.id = str(doc.header.id)
		self.issue_date = str(doc.header.issue_date_time)
		self.currency = str(doc.trade.settlement.currency_code)
		self.parse_seller(doc.trade.agreement.seller)
		self.parse_buyer(doc.trade.agreement.buyer)

		buyer_reference = doc.trade.agreement.buyer_order.issuer_assigned_id._text
		if (
			not self.purchase_order
			and buyer_reference
			and frappe.db.exists("Purchase Order", buyer_reference)
		):
			self.purchase_order = buyer_reference

		self.items = []
		for li in doc.trade.items.children:
			self.parse_line_item(li)

		self.taxes = []
		for tax in doc.trade.settlement.trade_tax.children:
			self.parse_tax(tax)

		self.payment_terms = []
		for term in doc.trade.settlement.terms.children:
			self.parse_payment_term(term)

		self.parse_monetary_summation(doc.trade.settlement.monetary_summation)
		self.parse_bank_details(doc.trade.settlement.payment_means)
		self.parse_billing_period(doc.trade.settlement.period)

	def _validate_schematron(self, xml_bytes):
		self.validation_errors = ""
		self.validation_warnings = ""
		xml_string = xml_bytes.decode("utf-8")

		try:
			validation_errors, validation_warnings = get_validation_errors(
				xml_string, EInvoiceProfile(self.profile)
			)
		except Exception:
			frappe.log_error(
				title="E Invoice schematron validation",
				reference_doctype=self.doctype,
				reference_name=self.name,
			)
			frappe.msgprint(
				_("Could not validate E Invoice schematron. See Error Log for details."),
				alert=True,
				indicator="orange",
			)
			return

		if any(validation_errors):
			self.e_invoice_is_correct = 0
			self.validation_errors += "\n".join(validation_errors)
		else:
			self.e_invoice_is_correct = 1

		if any(validation_warnings):
			self.validation_warnings += "\n".join(validation_warnings)

	def parse_seller(self, seller: TradeParty):
		self.seller_name = str(seller.name)
		self.seller_tax_id = (
			seller.tax_registrations.children[0].id._text if seller.tax_registrations.children else None
		)
		self.seller_electronic_address = str(seller.electronic_address.uri_ID._text)
		self.seller_electronic_address_scheme = str(seller.electronic_address.uri_ID._scheme_id)
		self.parse_address(seller.address, "seller")

	def parse_buyer(self, buyer: TradeParty):
		self.buyer_name = str(buyer.name)
		self.buyer_electronic_address = str(buyer.electronic_address.uri_ID._text)
		self.buyer_electronic_address_scheme = str(buyer.electronic_address.uri_ID._scheme_id)
		self.parse_address(buyer.address, "buyer")

	def parse_address(self, address: PostalTradeAddress, prefix: str) -> _dict:
		country = frappe.db.get_value("Country", {"code": str(address.country_id).lower()}, "name")

		self.set(f"{prefix}_city", str(address.city_name))
		self.set(f"{prefix}_address_line_1", str(address.line_one))
		self.set(f"{prefix}_address_line_2", str(address.line_two))
		self.set(f"{prefix}_postcode", str(address.postcode))
		self.set(f"{prefix}_country", str(country))

	def parse_line_item(self, li: LineItem):
		item = self.append("items")

		net_rate = float(li.agreement.net.amount._value)
		basis_qty = float(li.agreement.net.basis_quantity._amount or "1")
		rate = net_rate / basis_qty

		product_name_full = str(li.product.name)
		product_description = str(li.product.description)
		if len(product_name_full) > 140:
			item.product_name = product_name_full[:140]
			item.product_description = product_name_full + " | " + product_description
		else:
			item.product_name = product_name_full
			item.product_description = product_description
		item.seller_product_id = str(li.product.seller_assigned_id)
		item_code = str(li.product.buyer_assigned_id)
		if item_code and not frappe.db.exists("Item", item_code):
			item_code = None

		item.item = item_code or None
		item.billed_quantity = flt_or_none(li.delivery.billed_quantity._amount)
		item.unit_code = str(li.delivery.billed_quantity._unit_code)
		item.net_rate = rate
		item.tax_rate = flt_or_none(li.settlement.trade_tax.rate_applicable_percent._value)
		item.total_amount = flt_or_none(li.settlement.monetary_summation.total_amount._value)

	def parse_tax(self, tax: ApplicableTradeTax):
		t = self.append("taxes")
		t.basis_amount = flt_or_none(tax.basis_amount._value)
		t.rate_applicable_percent = flt_or_none(tax.rate_applicable_percent._value)
		t.calculated_amount = flt_or_none(tax.calculated_amount._value)

	def parse_payment_term(self, term: PaymentTerms):
		if not term.partial_amount.children:
			self.due_date = term.due._value
			return

		t = self.append("payment_terms")
		t.due = term.due._value
		partial_amount = None
		for row in term.partial_amount.children:
			if isinstance(row, tuple):
				# row = (amount, currency)
				if row[1] == self.currency:
					partial_amount = row[0]
					break
			else:
				# row = amount
				partial_amount = row
				break

		t.partial_amount = float(partial_amount) if partial_amount is not None else None
		t.description = term.description
		t.discount_basis_date = term.discount_terms.basis_date_time._value

		if term.discount_terms.calculation_percent._value:
			t.discount_calculation_percent = float(term.discount_terms.calculation_percent._value)

		if term.discount_terms.actual_amount._value:
			t.discount_actual_amount = float(term.discount_terms.actual_amount._value)

	def parse_monetary_summation(self, summation: MonetarySummation):
		self.line_total = flt_or_none(summation.line_total._value)
		self.allowance_total = flt_or_none(summation.allowance_total._value)
		self.charge_total = flt_or_none(summation.charge_total._value)
		self.tax_basis_total = flt_or_none(summation.tax_basis_total._amount)
		for value, currency in summation.tax_total_other_currency.children:
			if currency is None or currency == self.currency:
				self.tax_total = flt_or_none(value)
				break
		self.grand_total = flt_or_none(summation.grand_total._amount)
		self.total_prepaid = flt_or_none(summation.prepaid_total._value)
		self.due_payable = flt_or_none(summation.due_amount._value)

	def parse_bank_details(self, payment_means: PaymentMeans):
		self.payee_iban = payment_means.payee_account.iban._text or None

		if EInvoiceProfile(self.profile) >= EInvoiceProfile.EN16931:
			self.payee_account_name = payment_means.payee_account.account_name._text or None
			self.payee_bic = payment_means.payee_institution.bic._text or None

	def parse_billing_period(self, period: BillingSpecifiedPeriod):
		self.billing_period_start = period.start._value
		self.billing_period_end = period.end._value

	def guess_supplier(self):
		if self.supplier:
			return

		if frappe.db.exists("Supplier", self.seller_name):
			self.supplier = self.seller_name

		if self.seller_tax_id:
			self.supplier = frappe.db.get_value("Supplier", {"tax_id": self.seller_tax_id}, "name")

	def guess_company(self):
		if self.company:
			return

		if frappe.db.exists("Company", self.buyer_name):
			self.company = self.buyer_name
		else:
			self.company = get_default_company()

	def guess_uom(self):
		for row in self.items:
			if row.uom:
				continue

			if row.unit_code:
				rec20_3 = get_docnames_for("urn:xoev-de:kosit:codeliste:rec20_3", "UOM", row.unit_code)
				if rec20_3:
					row.uom = rec20_3[0]
				else:
					rec21_3 = get_docnames_for("urn:xoev-de:kosit:codeliste:rec21_3", "UOM", row.unit_code)
					if rec21_3:
						row.uom = rec21_3[0]
			elif row.item:
				stock_uom, purchase_uom = frappe.db.get_value("Item", row.item, ["stock_uom", "purchase_uom"])
				row.uom = purchase_uom or stock_uom

	def guess_item_code(self):
		for row in self.items:
			if row.item:
				continue

			if row.seller_product_id and self.supplier:
				row.item = frappe.db.get_value(
					"Item Supplier",
					{"supplier": self.supplier, "supplier_part_no": row.seller_product_id},
					"parent",
				)

	def guess_po_details(self):
		if not self.purchase_order:
			for pi_row in self.items:
				pi_row.po_detail = None
			return

		purchase_order = frappe.get_doc("Purchase Order", self.purchase_order)
		po_items = [
			frappe._dict(
				name=po_row.name,
				item_code=po_row.item_code,
				unbilled_amount=po_row.amount - po_row.billed_amt,
			)
			for po_row in purchase_order.items
		]
		for pi_row in self.items:
			if pi_row.po_detail and frappe.db.exists(
				"Purchase Order Item", {"name": pi_row.po_detail, "parent": self.purchase_order}
			):
				continue

			for po_row in po_items:
				if po_row.item_code == pi_row.item and po_row.unbilled_amount >= pi_row.total_amount:
					pi_row.po_detail = po_row.name
					po_row.unbilled_amount -= pi_row.total_amount
					break
			else:
				pi_row.po_detail = None

	def add_seller_product_ids_to_items(self):
		for row in self.items:
			try:
				# This is a convenience feature. Failure to update the Item data
				# should not prevent submission of the E Invoice Import.
				row.add_seller_product_id_to_item(self.supplier)
			except frappe.ValidationError:
				frappe.log_error(
					title="Failed to store Seller Product ID",
					reference_doctype=self.doctype,
					reference_name=self.name,
				)


def flt_or_none(value) -> float | None:
	return float(value) if value is not None else None


def get_xml_bytes(einvoice: str) -> bytes:
	"""Reads the XML data from the attached XML or PDF file."""
	CREATE_PI_ACTION = {
		"label": _("Create Purchase Invoice"),
		"client_action": "eu_einvoice.utils.new_purchase_invoice",
	}

	file = relative_url_to_path(einvoice)
	if file.suffix.lower() == ".pdf":
		xml_filename, xml_bytes = get_xml_from_pdf(file.read_bytes(), check_xsd=False)
		if not xml_bytes:
			frappe.throw(
				msg=_(
					"No machine-readable data was found in the PDF file. You can create a regular Purchase Invoice manually instead."
				),
				title=_("Not an E-Invoice"),
				primary_action=CREATE_PI_ACTION,
			)
	elif file.suffix.lower() == ".xml":
		xml_bytes = file.read_bytes()
	else:
		frappe.throw(
			msg=_(
				"The format of the uploaded file ({0}) is not supported for E-Invoices. Please upload a valid E-Invoice file or create a regular Purchase Invoice manually instead."
			).format(file.suffix),
			title=_("Unsupported file format"),
			primary_action=CREATE_PI_ACTION,
		)

	return xml_bytes


def relative_url_to_path(url: str) -> Path:
	"""Convert a relative URL to a file path."""
	return Path(get_site_path(url.lstrip("/"))).resolve()


@frappe.whitelist()
def create_purchase_invoice(source_name, target_doc=None):
	def post_process(source, target: PurchaseInvoice):
		target.set_missing_values()

	def process_item_row(source, target, source_parent) -> None:
		if source_parent.purchase_order:
			target.purchase_order = source_parent.purchase_order

	def process_tax_row(source, target, source_parent) -> None:
		target.charge_type = "Actual"

	def process_payment_term(source, target, source_parent):
		if source.discount_calculation_percent:
			target.discount_type = "Percentage"
			target.discount = source.discount_calculation_percent
		elif source.discount_actual_amount:
			target.discount_type = "Amount"
			target.discount = source.discount_actual_amount

	return get_mapped_doc(
		"E Invoice Import",
		source_name,
		{
			"E Invoice Import": {
				"doctype": "Purchase Invoice",
				"field_map": {
					"name": "e_invoice_import",
					"supplier": "supplier",
					"company": "company",
					"id": "bill_no",
					"issue_date": "bill_date",
					"currency": "currency",
					"billing_period_start": "from_date",
					"billing_period_end": "to_date",
				},
				# "field_no_map": ["items"],
			},
			"E Invoice Item": {
				"doctype": "Purchase Invoice Item",
				"field_map": {
					"item": "item_code",
					"billed_quantity": "qty",
					"uom": "uom",
					"net_rate": "rate",
					"po_detail": "po_detail",
				},
				"postprocess": process_item_row,
			},
			"E Invoice Trade Tax": {
				"doctype": "Purchase Taxes and Charges",
				"field_map": {
					"tax_account": "account_head",
					"rate_applicable_percent": "rate",
					"calculated_amount": "tax_amount",
				},
				"postprocess": process_tax_row,
			},
			"E Invoice Payment Term": {
				"doctype": "Payment Schedule",
				"field_map": {
					"due": "due_date",
					"partial_amount": "payment_amount",
					"description": "description",
					"discount_basis_date": "discount_date",
				},
				"postprocess": process_payment_term,
			},
		},
		target_doc,
		post_process,
	)


@frappe.whitelist()
def create_supplier(source_name, target_doc=None):
	return get_mapped_doc(
		"E Invoice Import",
		source_name,
		{
			"E Invoice Import": {
				"doctype": "Supplier",
				"field_map": {
					"seller_name": "supplier_name",
					"seller_tax_id": "tax_id",
					"seller_country": "country",
					"currency": "default_currency",
				},
			}
		},
		target_doc,
	)


@frappe.whitelist()
def create_supplier_address(source_name, target_doc=None):
	def post_process(source, target):
		target.append("links", {"link_doctype": "Supplier", "link_name": source.supplier})

	return get_mapped_doc(
		"E Invoice Import",
		source_name,
		{
			"E Invoice Import": {
				"doctype": "Address",
				"field_map": {
					"seller_address_line_1": "address_line1",
					"seller_address_line_2": "address_line2",
					"seller_city": "city",
					"seller_postcode": "pincode",
					"seller_country": "country",
				},
			}
		},
		target_doc,
		post_process,
	)


@frappe.whitelist()
def create_item(source_name, target_doc=None):
	def post_process(source, target):
		if frappe.db.get_single_value("Stock Settings", "item_naming_by") == "Item Code":
			target.item_code = target.item_name
		target.is_purchase_item = 1
		target.append(
			"supplier_items",
			{
				"supplier": frappe.db.get_value("E Invoice Import", source.parent, "supplier"),
				"supplier_part_no": source.seller_product_id,
			},
		)

	return get_mapped_doc(
		"E Invoice Item",
		source_name,
		{
			"E Invoice Item": {
				"doctype": "Item",
				"field_map": {
					"product_name": "item_name",
					"product_description": "description",
					"uom": "stock_uom",
				},
			}
		},
		target_doc,
		post_process,
	)


@frappe.whitelist()
def create_einvoice_from_po(source_name, target_doc=None):
	return get_mapped_doc(
		"Purchase Order",
		source_name,
		{
			"Purchase Order": {
				"doctype": "E Invoice Import",
				"field_map": {
					"name": "purchase_order",
				},
				"field_no_map": ["items"],
			}
		},
		target_doc,
	)


@frappe.whitelist()
def link_to_purchase_invoice(einvoice: str, purchase_invoice: str):
	"""Link an existing E Invoice Import to an existing Purchase Invoice."""
	pi = frappe.get_doc("Purchase Invoice", purchase_invoice)
	pi.check_permission("write")
	if pi.e_invoice_import:
		frappe.throw(
			_("Purchase Invoice {0} is already linked to E Invoice Import {1}").format(
				purchase_invoice, pi.e_invoice_import
			)
		)

	if not frappe.db.get_list("E Invoice Import", filters={"name": einvoice}, limit=1):
		frappe.throw(_("E Invoice Import {0} does not exist").format(einvoice))

	pi.db_set("e_invoice_import", einvoice)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def po_item_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	item_code = filters.pop("item_code", None)
	purchase_order = filters.pop("parent", None)

	if not purchase_order:
		return []

	purchase_order = frappe.get_cached_doc("Purchase Order", purchase_order)
	purchase_order.check_permission("read")

	results = [
		[
			row.name,
			_("Row {0}").format(row.idx),
			row.item_code,
			row.description[:100] + "..." if len(row.description) > 40 else row.description,
			row.get_formatted("qty") + " " + row.uom,
			row.get_formatted("net_rate") + " / " + row.uom,
		]
		for row in purchase_order.items
		if not item_code or row.item_code == item_code
	]

	if not txt:
		return results

	return [row for row in results if txt in ", ".join(row)]


@frappe.whitelist()
def get_po_item_details(po_detail: str):
	purchase_order_name = frappe.db.get_value("Purchase Order Item", po_detail, "parent")
	purchase_order = frappe.get_cached_doc("Purchase Order", purchase_order_name)
	if not purchase_order.has_permission("read"):
		return {}

	row = purchase_order.getone("items", {"name": po_detail})
	return {
		"item_code": row.item_code,
		"uom": row.uom,
	}
