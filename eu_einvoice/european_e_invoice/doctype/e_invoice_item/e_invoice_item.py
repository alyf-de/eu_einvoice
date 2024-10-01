# Copyright (c) 2024, ALYF GmbH and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class EInvoiceItem(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		billed_quantity: DF.Float
		item: DF.Link | None
		net_rate: DF.Currency
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		product_description: DF.SmallText | None
		product_name: DF.Data | None
		seller_product_id: DF.Data | None
		tax_rate: DF.Percent
		total_amount: DF.Currency
		unit_code: DF.Data | None
		uom: DF.Link | None
	# end: auto-generated types

	def add_seller_product_id_to_item(self, supplier: str):
		if not self.item or not self.seller_product_id:
			return

		if frappe.db.exists(
			"Item Supplier",
			{
				"supplier": supplier,
				"supplier_part_no": self.seller_product_id,
				"parenttype": "Item",
				"parent": self.item,
			},
		):
			return

		item_doc = frappe.get_doc("Item", self.item)
		item_doc.append(
			"supplier_items",
			{
				"supplier": supplier,
				"supplier_part_no": self.seller_product_id,
			},
		)
		item_doc.save()
