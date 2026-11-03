from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from lxml import objectify
from saxonche import PySaxonProcessor

from eu_einvoice.utils import EInvoiceProfile
from eu_einvoice.xml_security import strip_xml_doctype

PROFILE_TO_XSL = {
	EInvoiceProfile.BASIC: "Factur-X_1.07.2_BASIC.xsl",
	EInvoiceProfile.EN16931: "EN16931-CII-validation-preprocessed.xsl",
	EInvoiceProfile.EXTENDED: "Factur-X_1.07.2_EXTENDED.xsl",
	EInvoiceProfile.XRECHNUNG: "XRechnung-CII-validation.xsl",
}

# Ruleset pins for the bundled stylesheets (see README.md). Not the app version.
PROFILE_TO_RULESET_VERSION = {
	EInvoiceProfile.BASIC: "Factur-X 1.07.2 BASIC",
	EInvoiceProfile.EN16931: "EN 16931 CII (ConnectingEurope)",
	EInvoiceProfile.EXTENDED: "Factur-X 1.07.2 EXTENDED",
	# XR-MAJOR-MINOR-VERSION in XRechnung-CII-validation.xsl
	EInvoiceProfile.XRECHNUNG: "XRechnung 3.0",
}


def get_validation_errors(xml_string: str, profile: EInvoiceProfile):
	return get_errors_from_stylesheet(xml_string, PROFILE_TO_XSL[profile])


@lru_cache(maxsize=1)
def get_saxon_version() -> str:
	"""Return the installed SaxonC engine version (package or processor)."""
	try:
		with PySaxonProcessor(license=False) as proc:
			if proc.version:
				return str(proc.version)
	except Exception:
		pass
	try:
		return f"saxonche {version('saxonche')}"
	except PackageNotFoundError:
		return "SaxonC unknown"


def get_validator_version(profile: EInvoiceProfile | str | None = None) -> str:
	"""Human-readable ruleset + SaxonC version for audit logs / protocol."""
	ruleset = None
	if profile is not None:
		try:
			profile_enum = profile if isinstance(profile, EInvoiceProfile) else EInvoiceProfile(profile)
			ruleset = PROFILE_TO_RULESET_VERSION.get(profile_enum)
		except ValueError:
			ruleset = None
	if not ruleset:
		ruleset = "Schematron (bundled)"
	return f"{ruleset}; {get_saxon_version()}"


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
	# Same SaxonC XXE/SSRF surface as visualization - never parse untrusted DOCTYPE.
	xml_string = strip_xml_doctype(xml_string)
	with PySaxonProcessor(license=False) as proc:
		xslt30_processor = proc.new_xslt30_processor()
		input_node = proc.parse_xml(xml_text=xml_string)
		executable = xslt30_processor.compile_stylesheet(stylesheet_file=stylesheet_file)
		report = executable.transform_to_string(xdm_node=input_node)

	return report.encode("utf-8")
