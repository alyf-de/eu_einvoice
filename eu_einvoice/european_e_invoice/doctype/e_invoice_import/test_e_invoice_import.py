# Copyright (c) 2024, ALYF GmbH and Contributors
# See license.txt

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import frappe
from drafthorse.models.document import Document
from drafthorse.models.payment import PaymentMeans
from drafthorse.models.tradelines import LineItem
from frappe.tests import IntegrationTestCase
from lxml.etree import fromstring

from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import (
	EInvoiceImport,
	get_create_purchase_invoice_action,
	is_ubl_xml,
)

UBL_INVOICE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
	xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
	<cbc:ID>3360587</cbc:ID>
</Invoice>
"""

UBL_CREDIT_NOTE = b"""<?xml version="1.0" encoding="UTF-8"?>
<CreditNote xmlns="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
	xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
	<cbc:ID>CN-1</cbc:ID>
</CreditNote>
"""

CII_INVOICE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
	xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100">
	<rsm:ExchangedDocument>
		<ram:ID xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">1</ram:ID>
	</rsm:ExchangedDocument>
</rsm:CrossIndustryInvoice>
"""

UNSUPPORTED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<NotAnInvoice>
	<ID>1</ID>
</NotAnInvoice>
"""


class TestEInvoiceImport(IntegrationTestCase):
	def test_is_ubl_xml_detects_invoice_and_credit_note(self):
		self.assertTrue(is_ubl_xml(fromstring(UBL_INVOICE)))
		self.assertTrue(is_ubl_xml(fromstring(UBL_CREDIT_NOTE)))

	def test_is_ubl_xml_rejects_cii(self):
		self.assertFalse(is_ubl_xml(fromstring(CII_INVOICE)))

	def test_read_values_from_einvoice_rejects_ubl_with_action(self):
		for xml_bytes in (UBL_INVOICE, UBL_CREDIT_NOTE):
			with self.subTest(xml_bytes=xml_bytes[:80]):
				self._assert_unsupported_einvoice_throw(
					xml_bytes,
					message_snippet="UBL invoices are not supported",
				)

	def test_read_values_from_einvoice_rejects_unsupported_xml_with_action(self):
		self._assert_unsupported_einvoice_throw(
			UNSUPPORTED_XML,
			message_snippet="not a Cross-Industry-Invoice (CII)",
		)

	def test_read_values_from_einvoice_imports_cii(self):
		xml_bytes = make_cii_invoice_xml(
			invoice_id="INV-CII-1",
			seller_name="Seller GmbH",
			buyer_name="Buyer GmbH",
		)
		doc = frappe.new_doc("E Invoice Import")
		with patch.object(EInvoiceImport, "get_xml_bytes", return_value=xml_bytes):
			doc.read_values_from_einvoice()

		self.assertEqual(doc.id, "INV-CII-1")
		self.assertEqual(doc.currency, "EUR")
		self.assertEqual(doc.seller_name, "Seller GmbH")
		self.assertEqual(doc.buyer_name, "Buyer GmbH")
		self.assertEqual(doc.profile, "EN 16931")
		self.assertEqual(str(doc.issue_date), "2024-01-15")

	def _assert_unsupported_einvoice_throw(self, xml_bytes: bytes, message_snippet: str):
		frappe.flags.mute_messages = False
		frappe.clear_messages()
		doc = frappe.new_doc("E Invoice Import")

		with patch.object(EInvoiceImport, "get_xml_bytes", return_value=xml_bytes):
			with self.assertRaises(frappe.ValidationError) as cm:
				doc.read_values_from_einvoice()

		self.assertIn(message_snippet, str(cm.exception))
		message = frappe.get_message_log()[-1]
		self.assertEqual(message.get("title"), "Unsupported e-invoice format")
		self.assertEqual(message.get("primary_action"), get_create_purchase_invoice_action())

	def test_product_ids(self):
		supplier = "_Test Supplier"
		buyer_item = make_item("_Test E Invoice Buyer Item")
		seller_item = make_item(
			"_Test E Invoice Seller Item",
			supplier_items=[{"supplier": supplier, "supplier_part_no": "SUP-ART-2"}],
		)

		doc = frappe.new_doc("E Invoice Import")
		doc.supplier = supplier
		doc.parse_line_item(make_line_item(seller_assigned_id="SUP-ART-1", buyer_assigned_id=buyer_item))
		doc.parse_line_item(make_line_item(seller_assigned_id="SUP-ART-2"))
		doc.parse_line_item(make_line_item())
		doc.guess_item_code()

		self.assertEqual([row.seller_product_id for row in doc.items], ["SUP-ART-1", "SUP-ART-2", None])
		self.assertEqual([row.item for row in doc.items], [buyer_item, seller_item, None])

	def test_bank_details_from_payment_means_container(self):
		"""drafthorse >= 2025.2 exposes payment instructions (BG-16) as a container."""
		einvoice = Document()
		without_iban = PaymentMeans()
		without_iban.type_code = "ZZZ"
		einvoice.trade.settlement.payment_means.add(without_iban)
		with_iban = PaymentMeans()
		with_iban.type_code = "58"
		with_iban.payee_account.iban = "DE02120300000000202051"
		with_iban.payee_account.account_name = "Test GmbH"
		with_iban.payee_institution.bic = "BYLADEM1001"
		einvoice.trade.settlement.payment_means.add(with_iban)

		doc = frappe.new_doc("E Invoice Import")
		doc.profile = "EN 16931"
		doc.parse_bank_details(einvoice.trade.settlement.payment_means)

		self.assertEqual(doc.payee_iban, "DE02120300000000202051")
		self.assertEqual(doc.payee_account_name, "Test GmbH")
		self.assertEqual(doc.payee_bic, "BYLADEM1001")

	def test_bank_details_without_payment_means(self):
		doc = frappe.new_doc("E Invoice Import")
		doc.profile = "EN 16931"
		doc.parse_bank_details(Document().trade.settlement.payment_means)

		self.assertIsNone(doc.payee_iban)

	def test_bank_details_cleared_when_file_is_replaced(self):
		"""A replaced file without payment instructions must not keep the previous bank details."""
		doc = frappe.new_doc("E Invoice Import")
		doc.profile = "EN 16931"
		doc.payee_iban = "DE02120300000000202051"
		doc.payee_account_name = "Old Supplier GmbH"
		doc.payee_bic = "BYLADEM1001"

		doc.parse_bank_details(Document().trade.settlement.payment_means)

		self.assertIsNone(doc.payee_iban)
		self.assertIsNone(doc.payee_account_name)
		self.assertIsNone(doc.payee_bic)


def make_item(item_code: str, supplier_items=None) -> str:
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_group": "Products",
			"stock_uom": "Nos",
			"supplier_items": supplier_items or [],
		}
	)
	return item.insert().name


def make_line_item(seller_assigned_id=None, buyer_assigned_id=None) -> LineItem:
	li = LineItem()
	li.agreement.net.amount = Decimal("10")
	li.delivery.billed_quantity = (Decimal("1"), "C62")
	if seller_assigned_id:
		li.product.seller_assigned_id = seller_assigned_id
	if buyer_assigned_id:
		li.product.buyer_assigned_id = buyer_assigned_id
	return li


def make_cii_invoice_xml(invoice_id: str, seller_name: str, buyer_name: str) -> bytes:
	einvoice = Document()
	einvoice.context.guideline_parameter.id = "urn:cen.eu:en16931:2017"
	einvoice.header.id = invoice_id
	einvoice.header.type_code = "380"
	einvoice.header.issue_date_time = date(2024, 1, 15)
	einvoice.trade.agreement.seller.name = seller_name
	einvoice.trade.agreement.buyer.name = buyer_name
	einvoice.trade.settlement.currency_code = "EUR"
	summation = einvoice.trade.settlement.monetary_summation
	summation.line_total = Decimal("100.00")
	summation.tax_basis_total = Decimal("100.00")
	summation.tax_total = (Decimal("19.00"), "EUR")
	summation.grand_total = Decimal("119.00")
	summation.due_amount = Decimal("119.00")
	return einvoice.serialize(schema=None)
