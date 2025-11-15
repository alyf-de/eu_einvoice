# Copyright (c) 2025, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from eu_einvoice.european_e_invoice.api import test_validation


class TestEInvoiceAPI(FrappeTestCase):
	"""Test E-Invoice API endpoints."""

	def test_test_validation_with_sample_xml(self):
		"""Test that test_validation works with sample XML."""
		# Get sample XML
		sample_xml = self._get_sample_xml()

		# Save as file
		file_doc = frappe.get_doc({
			"doctype": "File",
			"file_name": "test_invoice.xml",
			"content": sample_xml,
			"is_private": 1
		})
		file_doc.insert()

		# Test validation
		result = test_validation(file_doc.file_url, "EN 16931")

		# Check result structure
		self.assertIn("success", result)
		self.assertIn("errors", result)
		self.assertIn("warnings", result)
		self.assertIn("profile", result)
		self.assertIn("error_count", result)
		self.assertIn("warning_count", result)

		# Clean up
		file_doc.delete()

	def test_test_validation_handles_invalid_profile(self):
		"""Test that test_validation handles invalid profile gracefully."""
		# Get sample XML
		sample_xml = self._get_sample_xml()

		# Save as file
		file_doc = frappe.get_doc({
			"doctype": "File",
			"file_name": "test_invoice.xml",
			"content": sample_xml,
			"is_private": 1
		})
		file_doc.insert()

		# Test with invalid profile - should not crash
		try:
			result = test_validation(file_doc.file_url, "INVALID_PROFILE")
			# Should return error result, not crash
			self.assertFalse(result["success"])
		except Exception as e:
			# Acceptable to throw validation error
			self.assertIn("Invalid profile", str(e))

		# Clean up
		file_doc.delete()

	def _get_sample_xml(self) -> str:
		"""Get sample EN 16931 XML."""
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
				<ram:Name>Test Company</ram:Name>
				<ram:SpecifiedTaxRegistration>
					<ram:ID schemeID="VA">DE123456789</ram:ID>
				</ram:SpecifiedTaxRegistration>
				<ram:URIUniversalCommunication>
					<ram:URIID schemeID="EM">test@example.com</ram:URIID>
				</ram:URIUniversalCommunication>
			</ram:SellerTradeParty>
			<ram:BuyerTradeParty>
				<ram:Name>Customer AG</ram:Name>
				<ram:URIUniversalCommunication>
					<ram:URIID schemeID="EM">customer@example.com</ram:URIID>
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
