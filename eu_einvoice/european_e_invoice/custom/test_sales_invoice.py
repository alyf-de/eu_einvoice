import io
import re
from datetime import date
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import frappe
from drafthorse.models.document import Document
from drafthorse.pdf import attach_xml
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from frappe.tests import IntegrationTestCase
from lxml import etree
from pypdf import PdfReader, PdfWriter

from eu_einvoice.european_e_invoice.custom.sales_invoice import (
	SalesInvoiceGenerator,
	duty_tax_fee_category_codes,
	get_einvoice,
	get_item_rate,
	get_xml_attachment_file_base_name,
	vat_exemption_reason_codes,
)
from eu_einvoice.schematron import get_validation_errors
from eu_einvoice.utils import EInvoiceProfile

NAMESPACES = {"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"}
# Schematron rules about VAT categories, rates and exemption reasons
VAT_RULES = r"BR-(O|E|AE|G|IC|Z|S)-\d+|BR-4[5-9]|BR-DE-14|BR-CO-17"


def map_code(retriever, code: str, doctype: str, name: str):
	"""Link `code` of the retriever's first code list to a record."""
	code_list = retriever.code_lists[0]
	if not frappe.db.exists("Code List", code_list):
		frappe.get_doc(doctype="Code List", __newname=code_list, title=code_list).insert()
		# ERPNext's resolve_code_list is request-cached, and the whole test run is one request.
		# An earlier test may have resolved this code list before it existed.
		frappe.local.request_cache.clear()

	frappe.get_doc(
		doctype="Common Code",
		code_list=code_list,
		title=code,
		common_code=code,
		applies_to=[{"link_doctype": doctype, "link_name": name}],
	).insert()


def get_vat_breakdowns(xml: bytes) -> list[dict]:
	"""Return category, rate, basis, tax amount and exemption reason of each VAT breakdown (BG-23)."""
	root = ET.fromstring(xml)
	breakdowns = []
	for tax in root.findall(".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax", NAMESPACES):
		breakdowns.append(
			{
				"category": tax.findtext("ram:CategoryCode", namespaces=NAMESPACES),
				"rate": float(tax.findtext("ram:RateApplicablePercent", namespaces=NAMESPACES)),
				"basis": float(tax.findtext("ram:BasisAmount", namespaces=NAMESPACES)),
				"tax": float(tax.findtext("ram:CalculatedAmount", namespaces=NAMESPACES)),
				"reason_code": tax.findtext("ram:ExemptionReasonCode", namespaces=NAMESPACES),
				"reason": tax.findtext("ram:ExemptionReason", namespaces=NAMESPACES),
			}
		)
	return breakdowns


def get_vat_errors(xml: bytes) -> list[str]:
	errors, _warnings = get_validation_errors(xml.decode(), EInvoiceProfile.EN16931)
	return [error for error in errors if re.search(VAT_RULES, error)]


class TestGetItemRate(IntegrationTestCase):
	def _taxes(self):
		return [
			frappe._dict(account_head="VAT 19%", charge_type="On Net Total", rate=0),
			frappe._dict(account_head="VAT 7%", charge_type="On Net Total", rate=0),
		]

	def test_other_accounts_at_zero_percent(self):
		self.assertEqual(get_item_rate('{"VAT 19%": 0, "VAT 7%": 7}', self._taxes()), 7)

	def test_other_accounts_not_applicable(self):
		self.assertEqual(get_item_rate('{"VAT 19%": "N/A", "VAT 7%": 7}', self._taxes()), 7)

	def test_all_matching_rows_not_applicable_returns_zero(self):
		"""The template says no invoice tax applies, so the line is 0% - not the row's rate."""
		taxes = [frappe._dict(account_head="VAT 19%", charge_type="On Net Total", rate=19)]
		self.assertEqual(get_item_rate('{"VAT 19%": "N/A"}', taxes), 0)

	def test_row_rate_applies_when_map_does_not_list_the_account(self):
		taxes = [frappe._dict(account_head="VAT 19%", charge_type="On Net Total", rate=19)]
		self.assertEqual(get_item_rate('{"VAT 5%": 5}', taxes), 19)
		self.assertEqual(get_item_rate(None, taxes), 19)


class TestVatExemptionReason(IntegrationTestCase):
	def test_empty_header_tax_skips_exemption_for_zero_rated(self):
		"""BR-Z-10: zero-rated VAT breakdown must not have an exemption reason."""
		invoice = frappe._dict(tax_category=None, taxes_and_charges=None, net_total=100)
		generator = SalesInvoiceGenerator(invoice, EInvoiceProfile.EN16931)
		generator.doc = Document()

		with patch.object(duty_tax_fee_category_codes, "get", return_value="Z"):
			generator._add_empty_tax()

		trade_tax = generator.doc.trade.settlement.trade_tax.children[0]
		self.assertEqual(trade_tax.category_code._text, "Z")
		self.assertEqual(trade_tax.rate_applicable_percent._value, 0)
		self.assertFalse(trade_tax.exemption_reason_code._text)

	def test_previous_row_vat_sets_exemption_reason(self):
		"""BR-*-10: VAT on an Actual charge also needs BT-120/BT-121 when exempt."""
		for charge_type, previous_field in (
			("On Previous Row Amount", "tax_amount"),
			("On Previous Row Total", "total"),
		):
			with self.subTest(charge_type=charge_type):
				previous = frappe._dict(
					account_head="_Test Freight",
					charge_type="Actual",
					description="Freight",
					tax_amount=50,
					total=50,
				)
				vat_row = frappe._dict(
					account_head="_Test Exempt VAT",
					charge_type=charge_type,
					rate=0,
					tax_amount=0,
				)
				invoice = frappe._dict(
					net_total=100,
					tax_category=None,
					taxes_and_charges=None,
					precision=lambda fieldname: 2,
					taxes=[previous, vat_row],
				)
				generator = SalesInvoiceGenerator(invoice, EInvoiceProfile.EXTENDED)
				generator.doc = Document()
				generator.item_tax_rates = set()

				with (
					patch.object(duty_tax_fee_category_codes, "get", return_value="E"),
					patch(
						"eu_einvoice.european_e_invoice.custom.sales_invoice.vat_exemption_reason_codes.get",
						return_value="vatex-eu-79-c",
					),
				):
					self.assertTrue(generator._add_taxes_and_charges())

				trade_tax = generator.doc.trade.settlement.trade_tax.children[0]
				self.assertEqual(trade_tax.type_code._text, "VAT")
				self.assertEqual(trade_tax.category_code._text, "E")
				self.assertEqual(trade_tax.basis_amount._value, getattr(previous, previous_field))
				self.assertEqual(trade_tax.exemption_reason_code._text, "VATEX-EU-79-C")


class TestNotSubjectToVatInvoice(IntegrationTestCase):
	"""Generate and validate a real Sales Invoice with VAT category "O" (not subject to VAT)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		tax_category = frappe.get_doc(doctype="Tax Category", title="_Test Not Subject to VAT").insert()
		cls.tax_category = tax_category.name
		item_tax_template = frappe.get_doc(
			doctype="Item Tax Template",
			title="_Test Not Subject to VAT",
			company="_Test Company",
			taxes=[{"tax_type": "_Test Account VAT - _TC", "tax_rate": 0}],
		).insert()
		cls.item_tax_template = item_tax_template.name

		# The category comes from the Tax Category, the exemption reason only from the line's
		# Item Tax Template. So the VAT breakdown must pick it up from the line items.
		map_code(duty_tax_fee_category_codes, "O", "Tax Category", cls.tax_category)
		map_code(vat_exemption_reason_codes, "vatex-eu-o", "Item Tax Template", cls.item_tax_template)

	def test_einvoice_for_category_o(self):
		for profile in (EInvoiceProfile.EN16931, EInvoiceProfile.XRECHNUNG):
			for with_tax_row in (False, True):
				with self.subTest(profile=profile, with_tax_row=with_tax_row):
					self._check_einvoice(profile, with_tax_row)

	def _check_einvoice(self, profile: EInvoiceProfile, with_tax_row: bool):
		invoice = create_sales_invoice(do_not_save=True, rate=100)
		invoice.einvoice_profile = profile.value
		invoice.tax_category = self.tax_category
		invoice.items[0].item_tax_template = self.item_tax_template
		invoice.terms = "Zahlbar & sofort"
		if with_tax_row:
			invoice.append(
				"taxes",
				{
					"charge_type": "On Net Total",
					"account_head": "_Test Account VAT - _TC",
					"description": "VAT",
					"rate": 0,
				},
			)
		invoice.insert()

		xml = get_einvoice(invoice)

		root = ET.fromstring(xml)
		note = root.find(".//ram:IncludedNote", NAMESPACES)
		self.assertEqual(note.findtext("ram:Content", namespaces=NAMESPACES), "Zahlbar & sofort")
		self.assertEqual(note.findtext("ram:SubjectCode", namespaces=NAMESPACES), "ABC")
		self.assertEqual(len(root.findall(".//ram:SpecifiedTradeSettlementPaymentMeans", NAMESPACES)), 1)

		line_tax = root.find(".//ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax", NAMESPACES)
		self.assertEqual(line_tax.findtext("ram:CategoryCode", namespaces=NAMESPACES), "O")
		# BR-O-05: no VAT rate on the line, not even 0
		self.assertIsNone(line_tax.find("ram:RateApplicablePercent", NAMESPACES))
		# The exemption reason belongs on the VAT breakdown only
		self.assertIsNone(line_tax.find("ram:ExemptionReasonCode", NAMESPACES))

		header_taxes = root.findall(
			".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax", NAMESPACES
		)
		self.assertEqual(len(header_taxes), 1)
		header_tax = header_taxes[0]
		self.assertEqual(header_tax.findtext("ram:CategoryCode", namespaces=NAMESPACES), "O")
		self.assertEqual(header_tax.findtext("ram:ExemptionReasonCode", namespaces=NAMESPACES), "VATEX-EU-O")
		# BR-48 allows to omit the rate for category O, but XRechnung requires it (BR-DE-14)
		header_rate = header_tax.find("ram:RateApplicablePercent", NAMESPACES)
		if profile == EInvoiceProfile.XRECHNUNG:
			self.assertEqual(float(header_rate.text), 0)
		else:
			self.assertIsNone(header_rate)

		# The test company is not set up completely, so only check the VAT rules.
		# XRechnung builds on EN 16931, so check against both.
		for validation_profile in {profile, EInvoiceProfile.EN16931}:
			errors, _warnings = get_validation_errors(xml.decode(), validation_profile)
			vat_errors = [error for error in errors if re.search(VAT_RULES, error)]
			self.assertEqual(vat_errors, [])

	def test_tax_account_exemption_reason_wins_over_line(self):
		"""The tax row's own Account mapping is more specific than a reason from a line item."""
		account = frappe.get_doc(
			doctype="Account",
			account_name="_Test Not Subject to VAT",
			parent_account="Duties and Taxes - _TC",
			company="_Test Company",
			account_type="Tax",
		).insert()
		map_code(vat_exemption_reason_codes, "vatex-eu-g", "Account", account.name)

		invoice = create_sales_invoice(do_not_save=True, rate=100)
		invoice.tax_category = self.tax_category
		invoice.items[0].item_tax_template = self.item_tax_template
		invoice.append(
			"taxes",
			{"charge_type": "On Net Total", "account_head": account.name, "description": "VAT", "rate": 0},
		)
		invoice.insert()

		root = ET.fromstring(get_einvoice(invoice))
		header_tax = root.find(".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax", NAMESPACES)
		self.assertEqual(header_tax.findtext("ram:ExemptionReasonCode", namespaces=NAMESPACES), "VATEX-EU-G")

	def test_category_o_omits_seller_and_buyer_vat_ids(self):
		"""BR-O-02: keep Tax IDs in ERPNext, but do not emit VA identifiers on category O invoices."""
		invoice = create_sales_invoice(do_not_save=True, rate=100)
		invoice.tax_category = self.tax_category
		invoice.items[0].item_tax_template = self.item_tax_template
		invoice.insert()
		# After insert: fetch_from company/customer.tax_id would overwrite values set earlier.
		invoice.company_tax_id = "DE123456789"
		invoice.tax_id = "DE987654321"

		xml = get_einvoice(invoice)
		root = ET.fromstring(xml)
		seller_vat_ids = root.findall(
			".//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']",
			NAMESPACES,
		)
		buyer_vat_ids = root.findall(
			".//ram:BuyerTradeParty/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']",
			NAMESPACES,
		)
		self.assertEqual(seller_vat_ids, [])
		self.assertEqual(buyer_vat_ids, [])

		errors, _warnings = get_validation_errors(xml.decode(), EInvoiceProfile.EN16931)
		br_o_02 = [error for error in errors if "BR-O-02" in error]
		self.assertEqual(br_o_02, [])

	def test_category_o_keeps_seller_and_buyer_tax_registration_fc(self):
		"""BR-O-02 only forbids VA (BT-31 / BT-48); FC may remain for seller and buyer."""
		invoice = create_sales_invoice(do_not_save=True, rate=100)
		invoice.tax_category = self.tax_category
		invoice.items[0].item_tax_template = self.item_tax_template
		invoice.insert()
		# After insert: fetch_from company/customer.tax_id would overwrite values set earlier.
		invoice.company_tax_id = "12/345/67890"
		invoice.tax_id = "98/765/43210"

		root = ET.fromstring(get_einvoice(invoice))
		seller_fc_ids = root.findall(
			".//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='FC']",
			NAMESPACES,
		)
		buyer_fc_ids = root.findall(
			".//ram:BuyerTradeParty/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='FC']",
			NAMESPACES,
		)
		self.assertEqual(len(seller_fc_ids), 1)
		self.assertEqual(seller_fc_ids[0].text, "12/345/67890")
		self.assertEqual(len(buyer_fc_ids), 1)
		self.assertEqual(buyer_fc_ids[0].text, "98/765/43210")


class TestApplicableTradeTaxes(IntegrationTestCase):
	def test_multi_tax_invoice_groups_and_basis_amounts(self):
		"""Unused tax rows are dropped, the basis comes from the lines with the same category and rate."""
		invoice = frappe._dict(
			net_total=1000,
			tax_category=None,
			taxes_and_charges=None,
			precision=lambda fieldname: 2,
			taxes=[
				# net_amount is ignored, ERPNext counts 0 % lines in as well
				frappe._dict(
					account_head="_Test VAT 19",
					charge_type="On Net Total",
					rate=19,
					tax_amount=19,
					net_amount=300,
				),
				frappe._dict(account_head="_Test VAT 7", charge_type="On Net Total", rate=7, tax_amount=7),
				# no line with this rate, so the basis is calculated from tax amount and rate
				frappe._dict(account_head="_Test VAT 5", charge_type="On Net Total", rate=5, tax_amount=0.5),
				# no tax amount, but a line item uses this rate (net amount 0), so it is kept
				frappe._dict(account_head="_Test VAT 3", charge_type="On Net Total", rate=3, tax_amount=0),
				# no tax amount and no line item uses this rate, so there is nothing to declare
				frappe._dict(account_head="_Test VAT 2", charge_type="On Net Total", rate=2, tax_amount=0),
			],
		)
		generator = SalesInvoiceGenerator(invoice, EInvoiceProfile.EN16931)
		generator.doc = Document()
		generator.item_tax_rates = {19, 7, 3, 0}
		generator.line_net_amounts = {("S", 19): 100, ("S", 7): 100, ("S", 3): 0, ("E", 0): 100}

		with patch.object(duty_tax_fee_category_codes, "get", return_value="S"):
			self.assertTrue(generator._add_taxes_and_charges())

		trade_taxes = generator.doc.trade.settlement.trade_tax.children
		self.assertEqual([t.rate_applicable_percent._value for t in trade_taxes], [19, 7, 5, 3])
		self.assertEqual([t.basis_amount._value for t in trade_taxes], [100, 100, 10, 0])


class TestMixedVatInvoice(IntegrationTestCase):
	"""Real Sales Invoices with several VAT categories and rates."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.vat_19 = cls._make_tax_account("_Test USt 19", 19)
		cls.vat_7 = cls._make_tax_account("_Test USt 7", 7)

		# Like older German setups: each template lists the other accounts at 0 %, not as N/A
		cls.standard = cls._make_template("_Test Regelsatz", {cls.vat_19: 19, cls.vat_7: 0})
		cls.reduced = cls._make_template("_Test Ermaessigt", {cls.vat_19: 0, cls.vat_7: 7})
		# Like ERPNext's current German setup: the other accounts are Not Applicable
		cls.standard_na = cls._make_template("_Test Regelsatz NA", {cls.vat_19: 19, cls.vat_7: None})
		cls.reduced_na = cls._make_template("_Test Ermaessigt NA", {cls.vat_19: None, cls.vat_7: 7})
		cls.exempt = cls._make_template(
			"_Test Steuerfrei", {cls.vat_19: 0, cls.vat_7: 0}, "Steuerfrei nach Paragraph 4 Nr. 14 UStG"
		)
		cls.intra_community = cls._make_template(
			"_Test Innergemeinschaftliche Lieferung", {cls.vat_19: 0}, "Innergemeinschaftliche Lieferung"
		)
		cls.reverse_charge = cls._make_template("_Test Reverse Charge Leistung EU", {cls.vat_19: 0})

		for template, category, reason in (
			(cls.exempt, "E", "vatex-eu-132"),
			(cls.intra_community, "K", "vatex-eu-ic"),
			(cls.reverse_charge, "AE", "vatex-eu-ae"),
		):
			map_code(duty_tax_fee_category_codes, category, "Item Tax Template", template)
			map_code(vat_exemption_reason_codes, reason, "Item Tax Template", template)

	@staticmethod
	def _make_tax_account(account_name: str, tax_rate: float) -> str:
		return (
			frappe.get_doc(
				doctype="Account",
				account_name=account_name,
				parent_account="Duties and Taxes - _TC",
				company="_Test Company",
				account_type="Tax",
				tax_rate=tax_rate,
			)
			.insert()
			.name
		)

	@staticmethod
	def _make_template(title: str, rates: dict[str, float], exemption_reason: str | None = None) -> str:
		return (
			frappe.get_doc(
				doctype="Item Tax Template",
				title=title,
				company="_Test Company",
				# A rate of None marks the account as Not Applicable
				taxes=[
					{"tax_type": account, "tax_rate": rate or 0, "not_applicable": rate is None}
					for account, rate in rates.items()
				],
				vat_exemption_reason_text=exemption_reason,
			)
			.insert()
			.name
		)

	def _make_invoice(self, templates: list[str], tax_accounts: list[str]):
		invoice = create_sales_invoice(do_not_save=True, rate=100)
		invoice.items = []
		for template in templates:
			invoice.append(
				"items", {"item_code": "_Test Item", "qty": 1, "rate": 100, "item_tax_template": template}
			)
		for account in tax_accounts:
			# Rate 0 on the row: the rate comes from the Item Tax Template
			invoice.append(
				"taxes",
				{"charge_type": "On Net Total", "account_head": account, "description": account, "rate": 0},
			)
		invoice.insert()
		# After insert: fetch_from company/customer.tax_id would overwrite values set earlier.
		invoice.company_tax_id = "DE123456789"
		invoice.tax_id = "FR12345678901"
		return invoice

	def test_standard_reduced_and_exempt(self):
		"""Each rate's basis is the sum of its own lines, the exempt line gets its own breakdown."""
		expected = [
			{"category": "S", "rate": 19, "basis": 100, "tax": 19, "reason_code": None, "reason": None},
			{"category": "S", "rate": 7, "basis": 100, "tax": 7, "reason_code": None, "reason": None},
			{
				"category": "E",
				"rate": 0,
				"basis": 100,
				"tax": 0,
				"reason_code": "VATEX-EU-132",
				"reason": "Steuerfrei nach Paragraph 4 Nr. 14 UStG",
			},
		]
		for standard, reduced in ((self.standard, self.reduced), (self.standard_na, self.reduced_na)):
			with self.subTest(standard=standard):
				invoice = self._make_invoice([standard, reduced, self.exempt], [self.vat_19, self.vat_7])
				self.assertEqual([tax.tax_amount for tax in invoice.taxes], [19, 7])

				xml = get_einvoice(invoice)

				self.assertEqual(get_vat_breakdowns(xml), expected)
				self.assertEqual(get_vat_errors(xml), [])

	def test_rates_from_lines_when_accounts_have_no_rate(self):
		"""Tax rows at 0 % on accounts without a rate take the rate their lines were charged with."""
		for account in (self.vat_19, self.vat_7):
			frappe.db.set_value("Account", account, "tax_rate", 0)
			self.addCleanup(
				frappe.db.set_value, "Account", account, "tax_rate", 19 if account == self.vat_19 else 7
			)

		invoice = self._make_invoice([self.standard, self.reduced], [self.vat_19, self.vat_7])
		xml = get_einvoice(invoice)

		self.assertEqual(
			[(b["rate"], b["basis"], b["tax"]) for b in get_vat_breakdowns(xml)], [(19, 100, 19), (7, 100, 7)]
		)
		self.assertEqual(get_vat_errors(xml), [])

	def test_single_rate_is_unchanged(self):
		"""One tax row and lines with one rate: a single breakdown over the net total."""
		invoice = self._make_invoice([self.standard, self.standard], [self.vat_19])

		breakdowns = get_vat_breakdowns(get_einvoice(invoice))

		self.assertEqual(
			[(b["category"], b["rate"], b["basis"], b["tax"]) for b in breakdowns], [("S", 19, 200, 38)]
		)

	def test_rates_as_charged_after_template_change(self):
		"""Later changes of the Item Tax Template don't change the e-invoice of an existing invoice."""
		invoice = self._make_invoice([self.reduced], [self.vat_7])
		template = frappe.get_doc("Item Tax Template", self.reduced)
		template.taxes[1].tax_rate = 5
		template.save()
		self.addCleanup(
			frappe.db.set_value, "Item Tax Template Detail", template.taxes[1].name, "tax_rate", 7
		)

		breakdowns = get_vat_breakdowns(get_einvoice(invoice))

		self.assertEqual([(b["rate"], b["basis"], b["tax"]) for b in breakdowns], [(7, 100, 7)])

	def test_intra_community_supply_and_reverse_charge(self):
		"""Two 0 % categories without any tax row need one breakdown each."""
		frappe.db.set_single_value("E Invoice Settings", "vat_exemption_reason_text", "Steuerschuldnerschaft")
		invoice = self._make_invoice([self.intra_community, self.reverse_charge, self.reverse_charge], [])
		invoice.shipping_address_name = invoice.customer_address  # BR-IC-12

		xml = get_einvoice(invoice)

		self.assertEqual(
			get_vat_breakdowns(xml),
			[
				{
					"category": "K",
					"rate": 0,
					"basis": 100,
					"tax": 0,
					"reason_code": "VATEX-EU-IC",
					"reason": "Innergemeinschaftliche Lieferung",
				},
				# no text on the template, so the default from E Invoice Settings is used
				{
					"category": "AE",
					"rate": 0,
					"basis": 200,
					"tax": 0,
					"reason_code": "VATEX-EU-AE",
					"reason": "Steuerschuldnerschaft",
				},
			],
		)
		self.assertEqual(get_vat_errors(xml), [])


class TestXmlAttachmentNaming(IntegrationTestCase):
	def test_auto_name_format_from_e_invoice_settings(self):
		doc = frappe._dict(name="SINV-00001", po_no="PO-42", doctype="Sales Invoice")
		field = "auto_name_format_for_xml_file"
		pattern = "XMLSTEM.-.{po_no}"
		real_gsv = frappe.get_single_value

		def get_single_value(doctype, fname, cache=True):
			if doctype == "E Invoice Settings" and fname == field:
				return pattern
			return real_gsv(doctype, fname, cache=cache)

		with patch.object(frappe, "get_single_value", side_effect=get_single_value):
			self.assertEqual(get_xml_attachment_file_base_name(doc), "XMLSTEM-PO-42")

	def test_empty_or_whitespace_uses_doc_name(self):
		doc = frappe._dict(name="SINV/00001", doctype="Sales Invoice")
		self.assertEqual(get_xml_attachment_file_base_name(doc, pattern=""), "SINV_00001")
		self.assertEqual(get_xml_attachment_file_base_name(doc, pattern="   "), "SINV_00001")

	def test_dot_separated_pattern_with_field(self):
		doc = frappe._dict(name="SINV-00001", po_no="PO-1", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="EXAMPLE.-.{po_no}")
		self.assertEqual(base, "EXAMPLE-PO-1")

	def test_dot_separated_inv_field_end(self):
		doc = frappe._dict(name="SINV-00001", po_no="X-9", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="INV.-.{po_no}.-.END")
		self.assertEqual(base, "INV-X-9-END")

	def test_leading_hashes_stripped_per_part_avoids_series_counter(self):
		doc = frappe._dict(name="SINV-00001", po_no="PO-1", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="EXAMPLE.-.{po_no}.#####")
		self.assertEqual(base, "EXAMPLE-PO-1")

	def test_hash_in_literal_sanitized_like_file_utils(self):
		doc = frappe._dict(name="SINV-00001", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="INV#X")
		self.assertEqual(base, "INV_X")

	def test_only_hash_segments_falls_back_to_doc_name(self):
		doc = frappe._dict(name="SINV-00001", doctype="Sales Invoice")
		self.assertEqual(get_xml_attachment_file_base_name(doc, pattern="#####"), "SINV-00001")

	def test_slash_in_resolved_base_is_sanitized(self):
		doc = frappe._dict(name="SINV-00001", po_no="A/B", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="{po_no}")
		self.assertEqual(base, "A_B")


class TestPaymentMeans(IntegrationTestCase):
	def _generator(self, profile: EInvoiceProfile):
		invoice = frappe._dict(
			company="Test & Partner GmbH",
			payment_terms_template="Test Terms",
			payment_schedule=[frappe._dict(mode_of_payment="Wire Transfer")],
		)
		generator = SalesInvoiceGenerator(invoice, profile)
		generator.doc = Document()
		return generator

	def test_payment_means(self):
		for profile in (EInvoiceProfile.BASIC, EInvoiceProfile.EN16931, EInvoiceProfile.XRECHNUNG):
			with self.subTest(profile=profile):
				generator = self._generator(profile)
				with (
					patch(
						"eu_einvoice.european_e_invoice.custom.sales_invoice.payment_means_codes.get",
						return_value="58",
					),
					patch(
						"eu_einvoice.european_e_invoice.custom.sales_invoice.get_bank_details",
						return_value=("DE02120300000000202051", "BYLADEM1001"),
					),
				):
					generator._add_payment_means()

				instructions = generator.doc.trade.settlement.payment_means.children
				self.assertEqual(len(instructions), 1)
				self.assertEqual(instructions[0].type_code._text, "58")
				self.assertEqual(instructions[0].payee_account.iban._text, "DE02120300000000202051")

				if profile >= EInvoiceProfile.EN16931:
					self.assertEqual(instructions[0].payee_account.account_name._text, "Test & Partner GmbH")
					self.assertEqual(instructions[0].payee_institution.bic._text, "BYLADEM1001")

	def test_payment_means_without_bank_account(self):
		"""BT-81 (payment means type code) is still required when there is no IBAN."""
		generator = self._generator(EInvoiceProfile.XRECHNUNG)
		with (
			patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice.payment_means_codes.get",
				return_value="ZZZ",
			),
			patch(
				"eu_einvoice.european_e_invoice.custom.sales_invoice.get_bank_details",
				return_value=(None, None),
			),
		):
			generator._add_payment_means()

		instructions = generator.doc.trade.settlement.payment_means.children
		self.assertEqual(len(instructions), 1)
		self.assertEqual(instructions[0].type_code._text, "ZZZ")
		self.assertFalse(instructions[0].payee_account.iban._text)


class TestPdfXmpMetadata(IntegrationTestCase):
	"""drafthorse < 2025.2 wrote unescaped user input into the XMP metadata (invalid PDF/A-3)."""

	def _pdf_metadata(self, seller_name: str) -> bytes:
		doc = Document()
		doc.context.guideline_parameter.id = "urn:cen.eu:en16931:2017"
		doc.header.id = "ACC-SINV-TEST-1"
		doc.header.type_code = "380"
		doc.header.issue_date_time = date(2026, 10, 7)
		doc.trade.agreement.seller.name = seller_name
		doc.trade.agreement.buyer.name = "Buyer"
		doc.trade.settlement.currency_code = "EUR"

		writer = PdfWriter()
		writer.add_blank_page(width=595, height=842)
		pdf = io.BytesIO()
		writer.write(pdf)

		zugferd_pdf = attach_xml(pdf.getvalue(), doc.serialize(schema=None), "EN 16931")
		return PdfReader(io.BytesIO(zugferd_pdf)).trailer["/Root"]["/Metadata"].get_object().get_data()

	def test_seller_name_with_ampersand_survives_in_xmp(self):
		xmp = etree.fromstring(self._pdf_metadata("Test & Partner GmbH"))  # raises on invalid XML
		self.assertIn("Test & Partner GmbH", list(xmp.itertext()))
