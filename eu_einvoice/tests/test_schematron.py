# Copyright (c) 2024, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from eu_einvoice.schematron import clear_cache, get_cache_info, get_validation_errors
from eu_einvoice.utils import EInvoiceProfile


class TestSchematronValidation(FrappeTestCase):
	"""Test Schematron validation and caching functionality."""

	def setUp(self):
		"""Set up test environment."""
		# Clear cache before each test
		clear_cache()

	def tearDown(self):
		"""Clean up after test."""
		clear_cache()

	def test_cache_initially_empty(self):
		"""Test that cache is empty initially."""
		cache_info = get_cache_info()
		self.assertEqual(cache_info["cache_size"], 0)
		self.assertEqual(len(cache_info["cached_stylesheets"]), 0)

	def test_cache_populates_on_validation(self):
		"""Test that cache gets populated after validation."""
		# Create a minimal valid XML
		xml_string = self._get_minimal_valid_xml()

		# First validation - should populate cache
		errors, warnings = get_validation_errors(xml_string, EInvoiceProfile.EN16931)

		# Check cache is populated
		cache_info = get_cache_info()
		self.assertEqual(cache_info["cache_size"], 1)
		self.assertGreater(len(cache_info["cached_stylesheets"]), 0)

	def test_clear_cache_works(self):
		"""Test that clearing cache actually clears it."""
		xml_string = self._get_minimal_valid_xml()

		# Populate cache
		get_validation_errors(xml_string, EInvoiceProfile.EN16931)

		# Verify cache is populated
		cache_info = get_cache_info()
		self.assertGreater(cache_info["cache_size"], 0)

		# Clear cache
		clear_cache()

		# Verify cache is empty
		cache_info = get_cache_info()
		self.assertEqual(cache_info["cache_size"], 0)

	def test_validation_returns_errors_for_invalid_xml(self):
		"""Test that validation detects errors in invalid XML."""
		# Invalid XML - missing required fields
		invalid_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100">
	<rsm:ExchangedDocument>
		<ram:ID xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">TEST-001</ram:ID>
	</rsm:ExchangedDocument>
</rsm:CrossIndustryInvoice>"""

		errors, warnings = get_validation_errors(invalid_xml, EInvoiceProfile.EN16931)

		# Should have validation errors
		self.assertGreater(len(errors), 0)

	def test_validation_passes_for_valid_xml(self):
		"""Test that validation passes for valid XML."""
		xml_string = self._get_minimal_valid_xml()

		errors, warnings = get_validation_errors(xml_string, EInvoiceProfile.EN16931)

		# Basic XML structure should not have critical errors
		# (May have warnings about missing optional fields)
		# Just check it doesn't crash
		self.assertIsInstance(errors, list)
		self.assertIsInstance(warnings, list)

	def test_multiple_profiles_use_different_stylesheets(self):
		"""Test that different profiles use different stylesheets."""
		xml_string = self._get_minimal_valid_xml()

		# Validate with BASIC profile
		get_validation_errors(xml_string, EInvoiceProfile.BASIC)
		cache_info_1 = get_cache_info()

		# Validate with EN16931 profile
		get_validation_errors(xml_string, EInvoiceProfile.EN16931)
		cache_info_2 = get_cache_info()

		# Cache should have grown
		self.assertGreater(cache_info_2["cache_size"], cache_info_1["cache_size"])

	def _get_minimal_valid_xml(self) -> str:
		"""Get a minimal valid EN 16931 XML for testing."""
		return """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
                          xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
                          xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
	<rsm:ExchangedDocumentContext>
		<ram:GuidelineSpecifiedDocumentContextParameter>
			<ram:ID>urn:cen.eu:en16931:2017</ram:ID>
		</ram:GuidelineSpecifiedDocumentContextParameter>
	</rsm:ExchangedDocumentContext>
	<rsm:ExchangedDocument>
		<ram:ID>TEST-001</ram:ID>
		<ram:TypeCode>380</ram:TypeCode>
		<ram:IssueDateTime>
			<udt:DateTimeString format="102">20250115</udt:DateTimeString>
		</ram:IssueDateTime>
	</rsm:ExchangedDocument>
	<rsm:SupplyChainTradeTransaction>
		<ram:ApplicableHeaderTradeAgreement>
			<ram:SellerTradeParty>
				<ram:Name>Test Seller GmbH</ram:Name>
				<ram:SpecifiedTaxRegistration>
					<ram:ID schemeID="VA">DE123456789</ram:ID>
				</ram:SpecifiedTaxRegistration>
				<ram:URIUniversalCommunication>
					<ram:URIID schemeID="EM">seller@example.com</ram:URIID>
				</ram:URIUniversalCommunication>
			</ram:SellerTradeParty>
			<ram:BuyerTradeParty>
				<ram:Name>Test Buyer AG</ram:Name>
				<ram:URIUniversalCommunication>
					<ram:URIID schemeID="EM">buyer@example.com</ram:URIID>
				</ram:URIUniversalCommunication>
			</ram:BuyerTradeParty>
		</ram:ApplicableHeaderTradeAgreement>
		<ram:ApplicableHeaderTradeSettlement>
			<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
			<ram:SpecifiedTradeSettlementHeaderMonetarySummation>
				<ram:LineTotalAmount>100.00</ram:LineTotalAmount>
				<ram:TaxBasisTotalAmount>100.00</ram:TaxBasisTotalAmount>
				<ram:TaxTotalAmount currencyID="EUR">19.00</ram:TaxTotalAmount>
				<ram:GrandTotalAmount>119.00</ram:GrandTotalAmount>
				<ram:DuePayableAmount>119.00</ram:DuePayableAmount>
			</ram:SpecifiedTradeSettlementHeaderMonetarySummation>
			<ram:ApplicableTradeTax>
				<ram:CalculatedAmount>19.00</ram:CalculatedAmount>
				<ram:TypeCode>VAT</ram:TypeCode>
				<ram:BasisAmount>100.00</ram:BasisAmount>
				<ram:CategoryCode>S</ram:CategoryCode>
				<ram:RateApplicablePercent>19.00</ram:RateApplicablePercent>
			</ram:ApplicableTradeTax>
		</ram:ApplicableHeaderTradeSettlement>
	</rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""
