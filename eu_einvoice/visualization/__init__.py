"""KoSIT XRechnung HTML visualization via SaxonC."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from lxml.etree import QName, XMLParser, fromstring

from eu_einvoice.saxon import transform_to_string

VISUALIZATION_DIR = Path(__file__).parent
VISUALIZATION_VERSION = "v2026-10-13"

UBL_NAMESPACE_PREFIX = "urn:oasis:names:specification:ubl:schema:xsd:"
CII_ROOT = "CrossIndustryInvoice"
CII_NS = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"

# Format detection must not resolve external entities (XXE / SSRF).
_SAFE_XML_PARSER = XMLParser(
	resolve_entities=False,
	no_network=True,
	dtd_validation=False,
	load_dtd=False,
)

# Desk iframe loads this HTML with scripts enabled but without same-origin.
# XSLT output is already escaped; security relies on sandbox + CSP (no regex HTML rewrite).
_VIEWER_CSP = (
	"default-src 'none'; "
	"img-src data: blob:; "
	"style-src 'unsafe-inline'; "
	"script-src 'unsafe-inline'; "
	"connect-src 'none'; "
	"form-action 'none'; "
	"base-uri 'none'; "
	"object-src 'none'; "
	"frame-src 'none'; "
	"worker-src 'none'"
)


class EInvoiceFormat(StrEnum):
	CII = "CII"
	UBL_INVOICE = "UBL Invoice"
	UBL_CREDIT_NOTE = "UBL CreditNote"


FORMAT_TO_XR_XSL = {
	EInvoiceFormat.CII: "cii-xr.xsl",
	EInvoiceFormat.UBL_INVOICE: "ubl-invoice-xr.xsl",
	EInvoiceFormat.UBL_CREDIT_NOTE: "ubl-creditnote-xr.xsl",
}


def detect_einvoice_format(xml_string: str | bytes) -> EInvoiceFormat | None:
	"""Return CII / UBL format from the XML root, or None if unsupported / not parseable."""
	try:
		payload = xml_string if isinstance(xml_string, bytes) else xml_string.encode("utf-8")
		root = fromstring(payload, parser=_SAFE_XML_PARSER)
	except Exception:
		return None

	qname = QName(root)
	local = qname.localname
	ns = qname.namespace or ""

	if local == CII_ROOT and ns == CII_NS:
		return EInvoiceFormat.CII
	if ns.startswith(UBL_NAMESPACE_PREFIX):
		if local == "Invoice":
			return EInvoiceFormat.UBL_INVOICE
		if local == "CreditNote":
			return EInvoiceFormat.UBL_CREDIT_NOTE
	return None


def transform_xml(xml_string: str, stylesheet_name: str) -> str:
	"""Run one XSLT stylesheet from this package against `xml_string`."""
	stylesheet_path = VISUALIZATION_DIR / stylesheet_name
	return transform_to_string(
		xml_string,
		str(stylesheet_path),
		cwd=str(VISUALIZATION_DIR),
	)


def render_einvoice_html(xml_string: str | bytes, lang: str = "de") -> str:
	"""Two-step KoSIT visualization: source XML -> XR intermediate -> HTML."""
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8")

	einvoice_format = detect_einvoice_format(xml_string)
	if not einvoice_format:
		raise ValueError("Unsupported e-invoice format for visualization")

	xr_xml = transform_xml(xml_string, FORMAT_TO_XR_XSL[einvoice_format])
	# Language is read by functions.xsl via the $lang stylesheet parameter.
	stylesheet_path = VISUALIZATION_DIR / "xrechnung-html.xsl"
	html = transform_to_string(
		xr_xml,
		str(stylesheet_path),
		cwd=str(VISUALIZATION_DIR),
		parameters={"lang": lang},
	)
	return _inject_csp_meta(_inject_frappe_viewer_styles(html))


def _inject_csp_meta(html: str) -> str:
	meta = f'<meta http-equiv="Content-Security-Policy" content="{_VIEWER_CSP}">'
	lower = html.lower()
	head_open = lower.find("<head")
	if head_open == -1:
		return meta + html
	gt = html.find(">", head_open)
	if gt == -1:
		return meta + html
	return html[: gt + 1] + meta + html[gt + 1 :]


def _inject_frappe_viewer_styles(html: str) -> str:
	"""Append Desk-like tab/chrome overrides without editing vendored KoSIT CSS."""
	override_path = VISUALIZATION_DIR / "frappe-viewer-override.css"
	override_css = override_path.read_text(encoding="utf-8")
	style_tag = f'<style id="eu-einvoice-viewer-override">\n{override_css}\n</style>'
	lower = html.lower()
	head_close = lower.rfind("</head>")
	if head_close != -1:
		return html[:head_close] + style_tag + html[head_close:]
	return style_tag + html
