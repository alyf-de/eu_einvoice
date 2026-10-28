from unittest.mock import patch
from xml.etree import ElementTree as ET

import frappe
from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from erpnext.controllers.sales_and_purchase_return import make_return_doc
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.custom.purchase_invoice import validate
from eu_einvoice.european_e_invoice.einvoice import (
	attach_xml_to_pdf,
	duty_tax_fee_category_codes,
	get_einvoice,
	vat_exemption_reason_codes,
)
from eu_einvoice.schematron import get_validation_errors
from eu_einvoice.utils import EInvoiceProfile

COMPANY = "_Test Company"
NS = {
	"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
	"rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
}
HEADER_TAX = ".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax"
LINE_TAX = ".//ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax"
IBAN = "DE02120300000000202051"


class TestSelfBilledPurchaseInvoice(IntegrationTestCase):
	"""Generate self-billed e-invoices from real Purchase Invoices and validate them with schematron."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.db.set_value("Company", COMPANY, {"tax_id": "DE123456789", "email": "invoices@example.com"})
		cls.billing_address = make_address("Company", COMPANY, "Germany", is_your_company_address=1)
		frappe.get_doc(
			doctype="Bank", bank_name="_Test Self-Billing Bank", swift_number="BYLADEM1001"
		).insert()

		cls.domestic = make_supplier("_Test Self-Billing Supplier", "Germany", "DE987654321")
		# §19 UStG small business: a tax number instead of a VAT ID
		cls.small_business = make_supplier("_Test Small Business Supplier", "Germany", "201/113/40209")
		cls.eu_supplier = make_supplier("_Test EU Self-Billing Supplier", "Austria", "ATU12345678")
		cls.no_bank = make_supplier("_Test Supplier Without Bank", "Germany", "DE111111111", with_bank=False)

		cls.small_business_category = (
			frappe.get_doc(doctype="Tax Category", title="_Test Small Business").insert().name
		)
		map_code(duty_tax_fee_category_codes, "E", "Tax Category", cls.small_business_category)
		map_code(vat_exemption_reason_codes, "vatex-eu-132", "Tax Category", cls.small_business_category)

		cls.reverse_charge_category = (
			frappe.get_doc(doctype="Tax Category", title="_Test Reverse Charge").insert().name
		)
		map_code(duty_tax_fee_category_codes, "AE", "Tax Category", cls.reverse_charge_category)
		cls.intra_community_category = (
			frappe.get_doc(doctype="Tax Category", title="_Test Intra-Community Supply").insert().name
		)
		map_code(duty_tax_fee_category_codes, "K", "Tax Category", cls.intra_community_category)

	def make_invoice(
		self, supplier: frappe._dict, profile: EInvoiceProfile | None, taxes=(), tax_category=None
	):
		invoice = make_purchase_invoice(supplier=supplier.name, qty=1, rate=100, do_not_save=True)
		invoice.einvoice_profile = profile.value if profile else None
		invoice.tax_category = tax_category
		invoice.supplier_address = supplier.address
		invoice.contact_person = supplier.contact
		invoice.billing_address = self.billing_address
		# Our receiving address, the deliver-to country for intra-community supplies (BR-IC-12)
		invoice.shipping_address = self.billing_address
		for tax in taxes:
			invoice.append("taxes", {"cost_center": "_Test Cost Center - _TC", **tax})
		return invoice.insert()

	def assert_valid_einvoice(self, invoice, profile: EInvoiceProfile) -> ET.Element:
		invoice.einvoice_profile = profile.value
		xml = get_einvoice(invoice).decode()
		validation_profiles = [profile]
		if profile == EInvoiceProfile.XRECHNUNG:
			# XRechnung builds on EN 16931, so check against both, like `validate_einvoice`
			validation_profiles.append(EInvoiceProfile.EN16931)

		for validation_profile in validation_profiles:
			errors, _warnings = get_validation_errors(xml, validation_profile)
			self.assertEqual(errors, [], f"{validation_profile.value} rejects the {profile.value} e-invoice")
		return ET.fromstring(xml)

	def test_domestic_vat(self):
		invoice = self.make_invoice(
			self.domestic,
			EInvoiceProfile.EN16931,
			taxes=[
				vat_row(19),
				# Only raises our stock value, so it must not show up in the e-invoice
				{
					"charge_type": "Actual",
					"category": "Valuation",
					"account_head": "_Test Account Customs Duty - _TC",
					"description": "Customs",
					"tax_amount": 10,
				},
			],
		)
		self.assertEqual(invoice.grand_total, 119)

		for profile in EInvoiceProfile:
			with self.subTest(profile=profile):
				root = self.assert_valid_einvoice(invoice, profile)
				self.assertEqual(root.findtext(".//rsm:ExchangedDocument/ram:TypeCode", namespaces=NS), "389")
				self.assertEqual(
					root.findtext(".//ram:SellerTradeParty/ram:Name", namespaces=NS), self.domestic.name
				)
				self.assertEqual(root.findtext(".//ram:BuyerTradeParty/ram:Name", namespaces=NS), COMPANY)
				self.assertEqual(
					root.findtext(".//ram:PayeePartyCreditorFinancialAccount/ram:IBANID", namespaces=NS), IBAN
				)
				header_taxes = root.findall(HEADER_TAX, NS)
				self.assertEqual(len(header_taxes), 1)
				self.assertEqual(float(header_taxes[0].findtext("ram:CalculatedAmount", namespaces=NS)), 19)
				self.assertEqual(float(root.findtext(".//ram:GrandTotalAmount", namespaces=NS)), 119)

	def test_small_business(self):
		invoice = self.make_invoice(
			self.small_business, EInvoiceProfile.EN16931, tax_category=self.small_business_category
		)

		for profile in (EInvoiceProfile.EN16931, EInvoiceProfile.XRECHNUNG):
			with self.subTest(profile=profile):
				root = self.assert_valid_einvoice(invoice, profile)
				header_tax = root.find(HEADER_TAX, NS)
				self.assertEqual(header_tax.findtext("ram:CategoryCode", namespaces=NS), "E")
				self.assertEqual(
					header_tax.findtext("ram:ExemptionReasonCode", namespaces=NS), "VATEX-EU-132"
				)
				self.assertEqual(
					root.findtext(
						".//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID", namespaces=NS
					),
					"201/113/40209",
				)

	def test_eu_reverse_charge(self):
		"""The VAT rows net to zero. The e-invoice ignores them and states one 0% breakdown."""
		for category, tax_category in (
			("AE", self.reverse_charge_category),
			("K", self.intra_community_category),
		):
			invoice = self.make_invoice(
				self.eu_supplier,
				EInvoiceProfile.EN16931,
				taxes=[vat_row(19), vat_row(19, add_deduct_tax="Deduct")],
				tax_category=tax_category,
			)
			self.assertEqual(invoice.grand_total, invoice.net_total)

			for profile in (EInvoiceProfile.EN16931, EInvoiceProfile.XRECHNUNG):
				with self.subTest(category=category, profile=profile):
					self.assert_reverse_charge(invoice, profile, category)

	def assert_reverse_charge(self, invoice, profile: EInvoiceProfile, category: str):
		root = self.assert_valid_einvoice(invoice, profile)
		self.assertEqual(root.find(LINE_TAX, NS).findtext("ram:CategoryCode", namespaces=NS), category)
		header_taxes = root.findall(HEADER_TAX, NS)
		self.assertEqual(len(header_taxes), 1)
		self.assertEqual(header_taxes[0].findtext("ram:CategoryCode", namespaces=NS), category)
		self.assertEqual(float(header_taxes[0].findtext("ram:BasisAmount", namespaces=NS)), 100)
		self.assertEqual(float(header_taxes[0].findtext("ram:CalculatedAmount", namespaces=NS)), 0)

	def test_return(self):
		original = self.make_invoice(self.domestic, EInvoiceProfile.EN16931, taxes=[vat_row(19)])
		original.submit()
		invoice = make_return_doc("Purchase Invoice", original.name)
		invoice.insert()

		for profile in (EInvoiceProfile.EN16931, EInvoiceProfile.XRECHNUNG):
			with self.subTest(profile=profile):
				root = self.assert_valid_einvoice(invoice, profile)
				self.assertEqual(root.findtext(".//rsm:ExchangedDocument/ram:TypeCode", namespaces=NS), "389")
				self.assertEqual(
					root.findtext(".//ram:InvoiceReferencedDocument/ram:IssuerAssignedID", namespaces=NS),
					original.name,
				)
				self.assertEqual(float(root.findtext(".//ram:GrandTotalAmount", namespaces=NS)), -119)

	def test_multiple_vat_rates(self):
		# Stands in for a 7% VAT account
		reduced_vat_account = "_Test Account Excise Duty - _TC"
		invoice = self.make_invoice(
			self.domestic,
			EInvoiceProfile.EN16931,
			taxes=[vat_row(19), {**vat_row(7), "account_head": reduced_vat_account}],
		)
		invoice.items[0].item_tax_template = make_item_tax_template(
			"_Test Self-Billing 19", {"_Test Account VAT - _TC": 19, reduced_vat_account: None}
		)
		invoice.append(
			"items",
			{
				"item_code": invoice.items[0].item_code,
				"qty": 1,
				"rate": 100,
				"expense_account": invoice.items[0].expense_account,
				"cost_center": invoice.items[0].cost_center,
				"item_tax_template": make_item_tax_template(
					"_Test Self-Billing 7", {"_Test Account VAT - _TC": None, reduced_vat_account: 7}
				),
			},
		)
		invoice.save()
		self.assertEqual(invoice.grand_total, 226)

		for profile in (EInvoiceProfile.EN16931, EInvoiceProfile.XRECHNUNG):
			with self.subTest(profile=profile):
				root = self.assert_valid_einvoice(invoice, profile)
				line_rates = [
					float(tax.findtext("ram:RateApplicablePercent", namespaces=NS))
					for tax in root.findall(LINE_TAX, NS)
				]
				self.assertEqual(line_rates, [19, 7])
				header_taxes = [
					(
						float(tax.findtext("ram:RateApplicablePercent", namespaces=NS)),
						float(tax.findtext("ram:BasisAmount", namespaces=NS)),
						float(tax.findtext("ram:CalculatedAmount", namespaces=NS)),
					)
					for tax in root.findall(HEADER_TAX, NS)
				]
				self.assertEqual(header_taxes, [(19, 100, 19), (7, 100, 7)])

	def test_amendment(self):
		original = self.make_invoice(self.domestic, EInvoiceProfile.EN16931, taxes=[vat_row(19)])
		original.submit()
		original.cancel()
		invoice = frappe.copy_doc(original)
		invoice.docstatus = 0
		invoice.amended_from = original.name
		invoice.insert()

		for profile in (EInvoiceProfile.EN16931, EInvoiceProfile.XRECHNUNG):
			with self.subTest(profile=profile):
				root = self.assert_valid_einvoice(invoice, profile)
				self.assertEqual(root.findtext(".//rsm:ExchangedDocument/ram:TypeCode", namespaces=NS), "384")
				self.assertEqual(
					root.findtext(".//ram:InvoiceReferencedDocument/ram:IssuerAssignedID", namespaces=NS),
					original.name,
				)

	def test_missing_supplier_bank_account(self):
		with patch("frappe.msgprint") as msgprint:
			invoice = self.make_invoice(self.no_bank, EInvoiceProfile.XRECHNUNG, taxes=[vat_row(19)])

		messages = [str(call.args[0]) for call in msgprint.call_args_list]
		self.assertTrue(any("no default bank account" in message for message in messages), messages)

		for profile in (EInvoiceProfile.EN16931, EInvoiceProfile.XRECHNUNG):
			with self.subTest(profile=profile):
				root = self.assert_valid_einvoice(invoice, profile)
				self.assertIsNone(root.find(".//ram:PayeePartyCreditorFinancialAccount", NS))

	def test_deducted_tax(self):
		"""Deducted taxes (e.g. withholding tax) are not supported in the e-invoice."""
		withholding_tax = {
			**vat_row(10, add_deduct_tax="Deduct"),
			"account_head": "_Test Account Excise Duty - _TC",
		}
		with patch("frappe.msgprint") as msgprint:
			self.make_invoice(self.domestic, EInvoiceProfile.EN16931, taxes=[vat_row(19), withholding_tax])

		messages = [str(call.args[0]) for call in msgprint.call_args_list]
		self.assertTrue(any("not be valid with deducted taxes" in message for message in messages), messages)

	def test_supplier_einvoice_and_internal_supplier(self):
		"""Both are unusual for a self-billed invoice, so we warn."""
		invoice = self.make_invoice(self.domestic, EInvoiceProfile.EN16931, taxes=[vat_row(19)])
		invoice.e_invoice_import = "Supplier's e-invoice"
		invoice.is_internal_supplier = 1

		with patch("frappe.msgprint") as msgprint:
			validate(invoice, "validate")

		messages = [str(call.args[0]) for call in msgprint.call_args_list]
		self.assertTrue(any("bill the same supply twice" in message for message in messages), messages)
		self.assertTrue(any("is an internal supplier" in message for message in messages), messages)

	def test_clearing_the_profile_resets_the_status(self):
		self.addCleanup(
			frappe.db.set_single_value,
			"E Invoice Settings",
			"validate_sales_invoice_on_save",
			frappe.db.get_single_value("E Invoice Settings", "validate_sales_invoice_on_save"),
		)
		frappe.db.set_single_value("E Invoice Settings", "validate_sales_invoice_on_save", 1)

		invoice = self.make_invoice(self.domestic, EInvoiceProfile.XRECHNUNG, taxes=[vat_row(19)])
		self.assertTrue(invoice.einvoice_is_correct, invoice.validation_errors)

		invoice.einvoice_profile = None
		invoice.save()
		self.assertFalse(invoice.einvoice_is_correct)
		self.assertFalse(invoice.validation_errors)

	def test_server_never_sets_the_profile(self):
		"""The supplier's profile is only a default in the form. Code that sets the profile wins."""
		supplier = frappe.get_doc(
			doctype="Supplier",
			supplier_name="_Test Supplier With Self-Billing Profile",
			supplier_group="All Supplier Groups",
			self_billing_einvoice_profile=EInvoiceProfile.XRECHNUNG.value,
		).insert()
		invoice = make_purchase_invoice(supplier=supplier.name, qty=1, rate=100, do_not_submit=True)
		self.assertFalse(invoice.einvoice_profile)

		invoice.einvoice_profile = EInvoiceProfile.EN16931.value
		invoice.supplier = self.domestic.name
		invoice.save()
		self.assertEqual(invoice.einvoice_profile, EInvoiceProfile.EN16931.value)

	def test_auto_attach_on_submit(self):
		"""Only self-billed invoices get the XML. The configured field belongs to Sales Invoice."""
		settings = frappe.get_single("E Invoice Settings")
		self.addCleanup(
			frappe.db.set_single_value,
			"E Invoice Settings",
			{
				"auto_attach_xml": settings.auto_attach_xml,
				"attach_field_for_xml_file": settings.attach_field_for_xml_file,
			},
		)
		# Set directly, because the settings only accept an Attach field of Sales Invoice
		frappe.db.set_single_value(
			"E Invoice Settings", {"auto_attach_xml": 1, "attach_field_for_xml_file": "custom_xml_file"}
		)

		self_billed = self.make_invoice(self.domestic, EInvoiceProfile.XRECHNUNG, taxes=[vat_row(19)])
		self_billed.submit()
		attachments = get_attachments(self_billed)
		self.assertEqual(len(attachments), 1)
		self.assertTrue(attachments[0].file_name.endswith(".xml"))
		self.assertFalse(attachments[0].attached_to_field)

		# ERPNext's own "Item Price added" message came with the first invoice
		with patch("frappe.msgprint") as msgprint:
			ordinary = self.make_invoice(self.domestic, None, taxes=[vat_row(19)])
			ordinary.submit()
		self.assertEqual(msgprint.call_args_list, [])
		self.assertEqual(get_attachments(ordinary), [])

	def test_pdf_without_profile_stays_unchanged(self):
		invoice = self.make_invoice(self.domestic, None)
		pdf_data = b"%PDF-1.4 not self-billed"
		self.assertIs(attach_xml_to_pdf(invoice.name, pdf_data, doctype="Purchase Invoice"), pdf_data)


def get_attachments(invoice) -> list[frappe._dict]:
	return frappe.get_all(
		"File",
		filters={"attached_to_doctype": invoice.doctype, "attached_to_name": invoice.name},
		fields=["file_name", "attached_to_field"],
	)


def make_item_tax_template(title: str, rates: dict[str, float | None]) -> str:
	"""Create an Item Tax Template. A rate of `None` marks a tax that doesn't apply to the item."""
	return (
		frappe.get_doc(
			doctype="Item Tax Template",
			title=title,
			company=COMPANY,
			taxes=[
				# Without the `not_applicable` field (older ERPNext), 0% rows count as not applicable
				{"tax_type": account, "tax_rate": rate or 0, "not_applicable": rate is None}
				for account, rate in rates.items()
			],
		)
		.insert()
		.name
	)


def vat_row(rate: float, add_deduct_tax: str = "Add") -> dict:
	return {
		"charge_type": "On Net Total",
		"category": "Total",
		"add_deduct_tax": add_deduct_tax,
		"account_head": "_Test Account VAT - _TC",
		"description": f"VAT {rate}%",
		"rate": rate,
	}


def make_address(link_doctype: str, link_name: str, country: str, **kwargs) -> str:
	return (
		frappe.get_doc(
			doctype="Address",
			address_title=link_name,
			address_type="Billing",
			address_line1="Hauptstraße 1",
			city="Berlin",
			pincode="10115",
			country=country,
			links=[{"link_doctype": link_doctype, "link_name": link_name}],
			**kwargs,
		)
		.insert()
		.name
	)


def make_supplier(supplier_name: str, country: str, tax_id: str, with_bank: bool = True) -> frappe._dict:
	"""Create a supplier with everything XRechnung needs from a seller."""
	supplier = frappe.get_doc(
		doctype="Supplier",
		supplier_name=supplier_name,
		supplier_group="All Supplier Groups",
		country=country,
		tax_id=tax_id,
	).insert()
	contact = frappe.get_doc(
		doctype="Contact",
		first_name="Erika",
		last_name="Mustermann",
		email_ids=[{"email_id": "erika@example.com", "is_primary": 1}],
		phone_nos=[{"phone": "+49 30 1234567", "is_primary_phone": 1}],
		links=[{"link_doctype": "Supplier", "link_name": supplier.name}],
	).insert()

	if with_bank:
		frappe.get_doc(
			doctype="Bank Account",
			account_name=supplier_name,
			bank="_Test Self-Billing Bank",
			party_type="Supplier",
			party=supplier.name,
			iban=IBAN,
			is_default=1,
		).insert()

	return frappe._dict(
		name=supplier.name,
		address=make_address("Supplier", supplier.name, country),
		contact=contact.name,
	)


def map_code(retriever, code: str, doctype: str, name: str):
	"""Map a record to a Common Code in the first code list of `retriever`."""
	code_list = retriever.code_lists[0]
	if not frappe.db.exists("Code List", code_list):
		frappe.get_doc(doctype="Code List", __newname=code_list, title=code_list).insert()

	frappe.get_doc(
		doctype="Common Code",
		code_list=code_list,
		title=code,
		common_code=code,
		applies_to=[{"link_doctype": doctype, "link_name": name}],
	).insert()
	# ERPNext's resolve_code_list is request-cached, and the whole test run is one request.
	# An earlier test may have resolved this code list before it existed.
	frappe.local.request_cache.clear()
