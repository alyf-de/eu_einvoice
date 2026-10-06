# Copyright (c) 2024, ALYF GmbH and Contributors
# See license.txt

from decimal import Decimal

import frappe
from drafthorse.models.party import TradeParty
from drafthorse.models.tradelines import LineItem
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import (
	payee_differs_from_seller,
)


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

	def test_guess_supplier(self):
		suffix = frappe.generate_hash(length=6)
		group = frappe.db.get_value("Supplier Group", {"is_group": 0})

		def make_supplier(name, supplier_name, tax_id=None):
			return (
				frappe.get_doc(
					doctype="Supplier", supplier_name=supplier_name, supplier_group=group, tax_id=tax_id
				)
				.insert(set_name=name)
				.name
			)

		def guess(**values):
			doc = frappe.new_doc("E Invoice Import")
			doc.update(values)
			doc.guess_supplier()
			return doc.supplier

		# supplier ID differs from supplier name (naming series)
		by_name = make_supplier(f"SUP-{suffix}-1", f"Name Match {suffix}")
		self.assertEqual(guess(seller_name=f"Name Match {suffix}"), by_name)

		# two suppliers with the same name: do not guess
		make_supplier(f"SUP-{suffix}-2", f"Twin {suffix}")
		make_supplier(f"SUP-{suffix}-3", f"Twin {suffix}")
		self.assertIsNone(guess(seller_name=f"Twin {suffix}"))

		# supplier ID equals seller name, unknown tax ID must not erase the match
		by_id = make_supplier(f"ID Match {suffix}", f"ID Match {suffix}")
		self.assertEqual(guess(seller_name=by_id, seller_tax_id=f"DE{suffix}"), by_id)

		# tax ID wins over an unrelated supplier whose ID equals the seller ID
		by_tax = make_supplier(f"SUP-{suffix}-4", f"Tax Match {suffix}", tax_id=f"DE-{suffix}")
		self.assertEqual(guess(seller_id=by_id, seller_tax_id=f"DE-{suffix}"), by_tax)

		# supplier found via the IBAN of its bank account
		by_iban = make_supplier(f"SUP-{suffix}-5", f"IBAN Match {suffix}")
		bank = frappe.get_doc(doctype="Bank", bank_name=f"Bank {suffix}").insert().name
		bank_account = frappe.get_doc(
			doctype="Bank Account",
			account_name=f"IBAN Match {suffix}",
			bank=bank,
			party_type="Supplier",
			party=by_iban,
			iban="ES9121000418450200051332",
		).insert()
		self.assertEqual(guess(seller_name="Unknown", payee_iban="ES9121000418450200051332"), by_iban)
		self.assertEqual(guess(seller_name="Unknown", payee_iban="ES91 2100 0418 4502 0005 1332"), by_iban)

		# an IBAN-only match must not beat the seller name (payee may differ from seller)
		self.assertEqual(
			guess(seller_name=f"Name Match {suffix}", payee_iban="ES9121000418450200051332"), by_name
		)

		# lowercase IBAN, and a bank account without party does not make the match ambiguous
		frappe.get_doc(
			doctype="Bank Account",
			account_name=f"Orphan {suffix}",
			bank=bank,
			party_type="Supplier",
			iban="ES9121000418450200051332",
		).insert()
		self.assertEqual(guess(seller_name="Unknown", payee_iban="es9121000418450200051332"), by_iban)

		# the IBAN of a payee that differs from the seller is not used
		doc = frappe.new_doc("E Invoice Import")
		doc.update({"seller_name": "Unknown", "payee_iban": "ES9121000418450200051332"})
		doc.flags.payee_differs = True
		doc.guess_supplier()
		self.assertIsNone(doc.supplier)

		# disabled bank accounts are ignored
		bank_account.db_set("disabled", 1)
		self.assertIsNone(guess(seller_name="Unknown", payee_iban="ES9121000418450200051332"))

	def test_payee_differs_from_seller(self):
		def party(name=None, id=None, legal_id=None):
			party = TradeParty()
			party.name = name
			party.id = id
			party.legal_organization.id = ("0002", legal_id) if legal_id else None
			return party

		seller = party("Seller GmbH", "S-1", "FR123")

		self.assertFalse(payee_differs_from_seller(seller, party()))  # no payee
		self.assertFalse(payee_differs_from_seller(seller, party("Seller GmbH")))
		self.assertFalse(payee_differs_from_seller(seller, party("Bank Name", "S-1")))
		self.assertFalse(payee_differs_from_seller(seller, party("Bank Name", legal_id="FR123")))
		self.assertTrue(payee_differs_from_seller(seller, party("Factoring AG", "F-1", "DE999")))
		self.assertTrue(payee_differs_from_seller(party("Seller GmbH"), party("Factoring AG")))

	def test_default_company_without_buyer_id(self):
		frappe.defaults.set_user_default("company", "_Test Company")
		self.addCleanup(frappe.defaults.clear_user_default, "company")

		doc = frappe.new_doc("E Invoice Import")
		doc.buyer_name = "Unknown Buyer GmbH"

		doc.guess_company()
		doc.guess_company_and_supplier()

		self.assertEqual(doc.company, "_Test Company")


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
