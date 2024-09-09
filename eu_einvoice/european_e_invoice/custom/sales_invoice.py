from decimal import Decimal

import frappe
from drafthorse.models.accounting import ApplicableTradeTax
from drafthorse.models.document import Document
from drafthorse.models.party import TaxRegistration
from drafthorse.models.payment import PaymentTerms
from drafthorse.models.tradelines import LineItem
from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data
from frappe.core.utils import html2text
from frappe.utils.data import flt


@frappe.whitelist()
def download_xrechnung(invoice_id: str):
	invoice = frappe.get_doc("Sales Invoice", invoice_id)
	invoice.check_permission("read")

	seller_address = None
	if invoice.company_address:
		seller_address = frappe.get_doc("Address", invoice.company_address)

	customer_address = None
	if invoice.customer_address:
		customer_address = frappe.get_doc("Address", invoice.customer_address)

	company = frappe.get_doc("Company", invoice.company)

	frappe.local.response.filename = f"{invoice_id}.xml"
	frappe.local.response.filecontent = get_xml(invoice, company, seller_address, customer_address)
	frappe.local.response.type = "download"


def get_xml(invoice, company, seller_address=None, customer_address=None):
	doc = Document()
	doc.context.guideline_parameter.id = "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended"
	doc.header.id = invoice.name

	# https://unece.org/fileadmin/DAM/trade/untdid/d16b/tred/tred1001.htm
	if invoice.is_return:
		# -- Credit note --
		# Document/message for providing credit information to the relevant party.
		doc.header.type_code = "381"
	elif invoice.amended_from:
		# -- Corrected invoice --
		# Commercial invoice that includes revised information differing from an
		# earlier submission of the same invoice.
		doc.header.type_code = "384"
	else:
		# -- Commercial invoice --
		# Document/message claiming payment for goods or services supplied under
		# conditions agreed between seller and buyer.
		doc.header.type_code = "380"

	doc.header.name = "RECHNUNG"
	doc.header.issue_date_time = invoice.posting_date
	if invoice.language:
		doc.header.languages.add(invoice.language)

	doc.trade.settlement.payee.name = invoice.customer_name
	doc.trade.settlement.invoicee.name = invoice.customer_name

	doc.trade.settlement.currency_code = invoice.currency
	doc.trade.settlement.payment_means.type_code = (
		# TODO: add as field in Mode of Payment
		# https://unece.org/fileadmin/DAM/trade/untdid/d16b/tred/tred4461.htm
		"ZZZ"
	)

	doc.trade.agreement.seller.name = invoice.company
	if invoice.company_tax_id:
		doc.trade.agreement.seller.tax_registrations.add(
			TaxRegistration(
				id=("VA", invoice.company_tax_id),
			)
		)

	if company.phone_no:
		doc.trade.agreement.seller.contact.telephone.number = company.phone_no
	if company.email:
		doc.trade.agreement.seller.contact.email.address = company.email

	if seller_address:
		doc.trade.agreement.seller.address.line_one = seller_address.address_line1
		doc.trade.agreement.seller.address.line_two = seller_address.address_line2
		doc.trade.agreement.seller.address.postcode = seller_address.pincode
		doc.trade.agreement.seller.address.city_name = seller_address.city
		doc.trade.agreement.seller.address.country_id = frappe.db.get_value(
			"Country", seller_address.country, "code"
		).upper()

	doc.trade.agreement.buyer.name = invoice.customer_name

	if invoice.po_no:
		doc.trade.agreement.buyer_reference = invoice.po_no
		doc.trade.agreement.buyer_order.issuer_assigned_id = invoice.po_no

	if invoice.po_date:
		doc.trade.agreement.buyer_order.issue_date_time = invoice.po_date

	if customer_address:
		doc.trade.agreement.buyer.address.line_one = customer_address.address_line1
		doc.trade.agreement.buyer.address.line_two = customer_address.address_line2
		doc.trade.agreement.buyer.address.postcode = customer_address.pincode
		doc.trade.agreement.buyer.address.city_name = customer_address.city
		doc.trade.agreement.buyer.address.country_id = frappe.db.get_value(
			"Country", customer_address.country, "code"
		).upper()

	for item in invoice.items:
		li = LineItem()
		li.document.line_id = str(item.idx)
		li.product.name = item.item_name
		li.product.seller_assigned_id = item.item_code
		li.product.buyer_assigned_id = item.customer_item_code
		li.product.description = html2text(item.description)
		net_amount = flt(item.net_amount, item.precision("net_amount"))
		li.agreement.net.amount = net_amount
		unit_code = frappe.db.get_value("UOM", item.uom, "common_code") or "C62"
		li.delivery.billed_quantity = (
			flt(item.qty, item.precision("qty")),
			unit_code,
		)

		if item.delivery_note:
			li.delivery.delivery_note.issuer_assigned_id = item.delivery_note
			li.delivery.delivery_note.issue_date_time = frappe.db.get_value(
				"Delivery Note", item.delivery_note, "posting_date"
			)

		li.settlement.trade_tax.type_code = "VAT"
		li.settlement.trade_tax.category_code = "S"
		li.settlement.monetary_summation.total_amount = item.amount
		doc.trade.items.add(li)

	tax_added = False
	for tax_row in get_itemised_tax_breakup_data(invoice):
		tax_row.pop("item")
		taxable_amount = tax_row.pop("taxable_amount")
		trade_tax = ApplicableTradeTax()
		trade_tax.basis_amount = round(taxable_amount, 2)
		for tax_name in tax_row.keys():
			tax_rate = tax_row[tax_name]["tax_rate"]
			tax_amount = tax_row[tax_name]["tax_amount"]
			if not tax_amount:
				continue

			trade_tax.calculated_amount = round(tax_amount, 2)
			trade_tax.rate_applicable_percent = Decimal("%.2f" % tax_rate)
			trade_tax.type_code = "VAT"
			trade_tax.category_code = "S"
			doc.trade.settlement.trade_tax.add(trade_tax)
			tax_added = True
			break

	if not tax_added:
		trade_tax = ApplicableTradeTax()
		trade_tax.type_code = "VAT"
		trade_tax.category_code = "S"
		trade_tax.calculated_amount = Decimal("0.00")
		trade_tax.rate_applicable_percent = Decimal("0.00")
		doc.trade.settlement.trade_tax.add(trade_tax)

	for ps in invoice.payment_schedule:
		payment_terms = PaymentTerms()
		payment_terms.description = ps.description
		payment_terms.due = ps.due_date
		payment_terms.partial_amount.add((ps.payment_amount, invoice.currency))
		if ps.discount and ps.discount_date:
			payment_terms.discount_terms.basis_date_time = ps.discount_date
			if ps.discount_type == "Percentage":
				payment_terms.discount_terms.calculation_percent = ps.discount
			elif ps.discount_type == "Amount":
				payment_terms.discount_terms.actual_amount = ps.discount

		doc.trade.settlement.terms.add(payment_terms)

	doc.trade.settlement.monetary_summation.line_total = invoice.total
	doc.trade.settlement.monetary_summation.charge_total = Decimal("0.00")
	doc.trade.settlement.monetary_summation.allowance_total = invoice.discount_amount
	doc.trade.settlement.monetary_summation.tax_basis_total = invoice.net_total
	doc.trade.settlement.monetary_summation.tax_total = invoice.total_taxes_and_charges
	doc.trade.settlement.monetary_summation.grand_total = invoice.grand_total
	doc.trade.settlement.monetary_summation.prepaid_total = invoice.total_advance
	doc.trade.settlement.monetary_summation.due_amount = invoice.outstanding_amount

	return doc.serialize(schema="FACTUR-X_EXTENDED")
