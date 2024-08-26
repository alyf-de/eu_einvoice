# Copyright (c) 2024, ALYF GmbH and contributors
# For license information, please see license.txt


from pathlib import Path
from typing import TYPE_CHECKING

import frappe
from drafthorse.models.document import Document as DrafthorseDocument
from erpnext import get_default_company
from frappe import _, _dict, get_site_path
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils.data import today

if TYPE_CHECKING:
	from drafthorse.models.party import PostalTradeAddress, TradeParty
	from drafthorse.models.tradelines import LineItem
	from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice


class EInvoiceImport(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from eu_einvoice.european_e_invoice.doctype.e_invoice_item.e_invoice_item import EInvoiceItem

		amended_from: DF.Link | None
		buyer_address_line_1: DF.Data | None
		buyer_address_line_2: DF.Data | None
		buyer_city: DF.Data | None
		buyer_country: DF.Link | None
		buyer_name: DF.Data | None
		buyer_postcode: DF.Data | None
		company: DF.Link | None
		currency: DF.Link | None
		einvoice: DF.Attach | None
		id: DF.Data | None
		issue_date: DF.Date | None
		items: DF.Table[EInvoiceItem]
		seller_address_line_1: DF.Data | None
		seller_address_line_2: DF.Data | None
		seller_city: DF.Data | None
		seller_country: DF.Link | None
		seller_name: DF.Data | None
		seller_postcode: DF.Data | None
		seller_tax_id: DF.Data | None
		supplier: DF.Link | None
		supplier_address: DF.Link | None
	# end: auto-generated types

	def before_save(self):
		if self.einvoice and self.has_value_changed("einvoice"):
			self.parse_einvoice()

	def before_submit(self):
		if not self.supplier:
			frappe.throw(_("Please create or select a supplier before submitting"))

		if not self.company:
			frappe.throw(_("Please select a company before submitting"))

		if not (self.items and all(row.item for row in self.items)):
			frappe.throw(_("Please map all invoice lines to an item before submitting"))

		self.create_purchase_invoice()

	def parse_einvoice(self):
		path_to_einvoice = Path(get_site_path(self.einvoice.lstrip("/"))).resolve()
		xml = path_to_einvoice.read_bytes()
		doc = DrafthorseDocument.parse(xml)
		self.id = str(doc.header.id)
		self.issue_date = str(doc.header.issue_date_time)
		self.currency = str(doc.trade.settlement.currency_code)
		self.parse_seller(doc.trade.agreement.seller)
		self.parse_buyer(doc.trade.agreement.buyer)

		self.items = []
		for li in doc.trade.items.children:
			self.parse_line_item(li)

	def parse_seller(self, seller: "TradeParty"):
		self.seller_name = str(seller.name)
		if frappe.db.exists("Supplier", self.seller_name):
			self.supplier = self.seller_name
		self.seller_tax_id = (
			seller.tax_registrations.children[0].id._text if seller.tax_registrations.children else None
		)
		if self.seller_tax_id and not self.supplier:
			self.supplier = frappe.db.get_value("Supplier", {"tax_id": self.seller_tax_id}, "name")

		self.parse_address(seller.address, "seller")

	def parse_buyer(self, buyer: "TradeParty"):
		self.buyer_name = str(buyer.name)
		if frappe.db.exists("Company", self.buyer_name):
			self.company = self.buyer_name
		else:
			self.company = get_default_company()

		self.parse_address(buyer.address, "buyer")

	def parse_address(self, address: "PostalTradeAddress", prefix: str) -> _dict:
		country = frappe.db.get_value("Country", {"code": str(address.country_id).lower()}, "name")

		self.set(f"{prefix}_city", str(address.city_name))
		self.set(f"{prefix}_address_line_1", str(address.line_one))
		self.set(f"{prefix}_address_line_2", str(address.line_two))
		self.set(f"{prefix}_postcode", str(address.postcode))
		self.set(f"{prefix}_country", str(country))

	def parse_line_item(self, li: "LineItem"):
		item = self.append("items")
		supplier = None

		net_rate = float(li.agreement.net.amount._value)
		basis_qty = float(li.agreement.net.basis_quantity._amount or "1")
		rate = net_rate / basis_qty

		item.product_name = str(li.product.name)
		item.product_description = str(li.product.description)
		item.seller_product_id = str(li.product.seller_assigned_id)
		item_code = str(li.product.buyer_assigned_id)
		if item_code and not frappe.db.exists("Item", item_code):
			item_code = None

		if item.seller_product_id and supplier and not item_code:
			item_code = frappe.db.get_value(
				"Item Supplier", {"supplier": supplier, "supplier_part_no": item.seller_product_id}, "parent"
			)
		item.item = item_code

		item.billed_quantity = float(li.delivery.billed_quantity._amount)
		item.unit_code = str(li.delivery.billed_quantity._unit_code)
		item.uom = frappe.db.get_value("UOM", {"common_code": item.unit_code}, "name")

		item.net_rate = rate
		item.tax_rate = float(li.settlement.trade_tax.rate_applicable_percent._value)
		item.total_amount = float(li.settlement.monetary_summation.total_amount._value)

	def create_purchase_invoice(self):
		pi: "PurchaseInvoice" = frappe.new_doc("Purchase Invoice")
		pi.supplier = self.supplier
		pi.company = self.company
		pi.posting_date = today()
		pi.bill_no = self.id
		pi.bill_date = self.issue_date
		pi.currency = self.currency
		for item in self.items:
			pi.append(
				"items",
				{
					"item_code": item.item,
					"qty": item.billed_quantity,
					"uom": item.uom,
					"rate": item.net_rate,
				},
			)

		# TODO: add back-link to Purchase Invoice
		# pi.einvoice_import = self.name
		pi.set_missing_values()
		pi.insert(ignore_mandatory=True)


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
def create_item(source_doc, target_doc=None):
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
		source_doc,
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
