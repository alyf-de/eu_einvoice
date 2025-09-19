from pathlib import Path

from lxml import objectify
from saxonche import PySaxonProcessor

from eu_einvoice.utils import EInvoiceProfile

PROFILE_TO_XSL = {
	EInvoiceProfile.BASIC: "Factur-X_1.07.2_BASIC.xsl",
	EInvoiceProfile.EN16931: "EN16931-CII-validation-preprocessed.xsl",
	EInvoiceProfile.EXTENDED: "Factur-X_1.07.2_EXTENDED.xsl",
	EInvoiceProfile.XRECHNUNG: "XRechnung-CII-validation.xsl",
}


def get_validation_errors(xml_string: str, profile: EInvoiceProfile):
	return get_errors_from_stylesheet(xml_string, PROFILE_TO_XSL[profile])


def get_errors_from_stylesheet(xml_string: str, stylesheet: str):
	stylesheet_path = Path(__file__).parent / stylesheet
	report = get_validation_report(xml_string, str(stylesheet_path))
	return extract_failed_asserts(report)


def extract_failed_asserts(xml: bytes) -> tuple[list[str], list[str]]:
	root = objectify.fromstring(xml)
	failed_asserts = root.xpath(
		"//svrl:failed-assert/svrl:text",
		namespaces={"svrl": "http://purl.oclc.org/dsdl/svrl"},
	)
	warnings = root.xpath(
		"//svrl:successful-report/svrl:text",
		namespaces={"svrl": "http://purl.oclc.org/dsdl/svrl"},
	)
	errors = [failed_assert.text.strip() for failed_assert in failed_asserts if failed_assert.text]
	warnings = [warning.text.strip() for warning in warnings if warning.text]
	return errors, warnings


def get_validation_report(xml_string: str, stylesheet_file: str) -> bytes:
	with PySaxonProcessor(license=False) as proc:
		xslt30_processor = proc.new_xslt30_processor()
		input_node = proc.parse_xml(xml_text=xml_string)
		executable = xslt30_processor.compile_stylesheet(stylesheet_file=stylesheet_file)
		report = executable.transform_to_string(xdm_node=input_node)

	return report.encode("utf-8")
