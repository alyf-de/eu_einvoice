# Copyright (c) 2024, ALYF GmbH and Contributors
# See license.txt

from decimal import Decimal

import frappe
from drafthorse.models.document import Document
from drafthorse.models.payment import PaymentMeans
from drafthorse.models.tradelines import LineItem
from frappe.tests import IntegrationTestCase


class TestEInvoiceImport(IntegrationTestCase):
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
