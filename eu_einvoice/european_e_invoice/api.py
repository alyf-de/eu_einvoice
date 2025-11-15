"""API endpoints for European E-Invoice functionality."""

import frappe
from frappe import _


@frappe.whitelist()
def test_validation(test_file: str, profile: str):
	"""Test e-invoice validation for an uploaded file.

	Args:
	    test_file: URL of the uploaded XML or PDF file
	    profile: E-invoice profile to validate against (BASIC, EN 16931, EXTENDED, XRECHNUNG)

	Returns:
	    Dict with validation results
	"""
	from eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import import get_xml_bytes
	from eu_einvoice.schematron import get_validation_errors
	from eu_einvoice.utils import EInvoiceProfile

	try:
		# Get XML bytes from file
		xml_bytes = get_xml_bytes(test_file)
		xml_string = xml_bytes.decode("utf-8")

		# Parse profile
		try:
			einvoice_profile = EInvoiceProfile(profile)
		except ValueError:
			frappe.throw(_("Invalid profile: {0}").format(profile))

		# Validate
		errors, warnings = get_validation_errors(xml_string, einvoice_profile)

		# Additional EN 16931 validation for XRechnung if enabled
		if einvoice_profile == EInvoiceProfile.XRECHNUNG:
			settings = frappe.get_single("E Invoice Settings")
			if settings.validate_xrechnung_against_en16931:
				en16931_errors, en16931_warnings = get_validation_errors(xml_string, EInvoiceProfile.EN16931)
				errors.extend(en16931_errors)
				warnings.extend(en16931_warnings)

		return {
			"success": len(errors) == 0,
			"errors": errors,
			"warnings": warnings,
			"profile": profile,
			"error_count": len(errors),
			"warning_count": len(warnings),
		}

	except Exception as e:
		frappe.log_error(title="E-Invoice Validation Test Failed", message=str(e))
		return {
			"success": False,
			"errors": [str(e)],
			"warnings": [],
			"profile": profile,
			"error_count": 1,
			"warning_count": 0,
		}


@frappe.whitelist()
def download_sample_invoice():
	"""Download a sample e-invoice XML file.

	Returns:
	    Sample XML file as download
	"""
	sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
                          xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
                          xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
                          xmlns:qdt="urn:un:unece:uncefact:data:standard:QualifiedDataType:100">
	<rsm:ExchangedDocumentContext>
		<ram:GuidelineSpecifiedDocumentContextParameter>
			<ram:ID>urn:cen.eu:en16931:2017</ram:ID>
		</ram:GuidelineSpecifiedDocumentContextParameter>
	</rsm:ExchangedDocumentContext>
	<rsm:ExchangedDocument>
		<ram:ID>SAMPLE-001</ram:ID>
		<ram:TypeCode>380</ram:TypeCode>
		<ram:IssueDateTime>
			<udt:DateTimeString format="102">20250115</udt:DateTimeString>
		</ram:IssueDateTime>
	</rsm:ExchangedDocument>
	<rsm:SupplyChainTradeTransaction>
		<ram:ApplicableHeaderTradeAgreement>
			<ram:SellerTradeParty>
				<ram:Name>Sample Company GmbH</ram:Name>
				<ram:SpecifiedTaxRegistration>
					<ram:ID schemeID="VA">DE123456789</ram:ID>
				</ram:SpecifiedTaxRegistration>
			</ram:SellerTradeParty>
			<ram:BuyerTradeParty>
				<ram:Name>Customer AG</ram:Name>
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

	frappe.local.response.filename = "sample-einvoice-en16931.xml"
	frappe.local.response.filecontent = sample_xml
	frappe.local.response.type = "download"
