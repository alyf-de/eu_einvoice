# Copyright (c) 2024, ALYF GmbH and contributors
# For license information, please see license.txt

# import frappe
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
