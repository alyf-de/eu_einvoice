# Copyright (c) 2024, ALYF GmbH and Contributors
# See license.txt

from decimal import Decimal

import frappe
from drafthorse.models.tradelines import LineItem
from frappe.tests.utils import FrappeTestCase


class TestEInvoiceImport(FrappeTestCase):
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
