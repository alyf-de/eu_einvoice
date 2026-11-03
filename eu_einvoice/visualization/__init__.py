"""KoSIT XRechnung HTML visualization via SaxonC."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from lxml.etree import QName, XMLParser, fromstring
from saxonche import PySaxonProcessor

from eu_einvoice.xml_security import strip_xml_doctype

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
# Neutralize dangerous URL schemes and unexpected event handlers from invoice data.
_ATTR_URL_RE = re.compile(
	r"""(?P<prefix>\s(?:href|src|action|xlink:href)\s*=\s*["'])(?P<url>[^"']*)(?P<suffix>["'])""",
	re.IGNORECASE,
)
# Quoted values and unquoted HTML attribute values (e.g. onerror=alert(1)).
_ON_ATTR_RE = re.compile(
	r"""\s+on[a-z]+\s*=\s*(?:(?P<quote>["'])(?P<qvalue>.*?)(?P=quote)|(?P<uvalue>[^\s>]+))""",
	re.IGNORECASE | re.DOTALL,
)
# Trusted KoSIT handlers from xrechnung-html.xsl / xrechnung-viewer.js only:
# - tab switching: show(this) / show(this);
# - attachment download: downloadData('download-d0e123') via generate-id()
_SAFE_ONCLICK = re.compile(r"^(?:show\(this\);?|downloadData\('download-[A-Za-z0-9_-]+'\))$")

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

	if local == CII_ROOT or ns == CII_NS:
		return EInvoiceFormat.CII
	if ns.startswith(UBL_NAMESPACE_PREFIX):
		if local == "Invoice":
			return EInvoiceFormat.UBL_INVOICE
		if local == "CreditNote":
			return EInvoiceFormat.UBL_CREDIT_NOTE
	return None


def transform_xml(xml_string: str, stylesheet_name: str) -> str:
	"""Run one XSLT stylesheet from this package against `xml_string`."""
	xml_string = strip_xml_doctype(xml_string)
	stylesheet_path = VISUALIZATION_DIR / stylesheet_name
	with PySaxonProcessor(license=False) as proc:
		xslt30_processor = proc.new_xslt30_processor()
		xslt30_processor.set_cwd(str(VISUALIZATION_DIR))
		input_node = proc.parse_xml(xml_text=xml_string)
		executable = xslt30_processor.compile_stylesheet(stylesheet_file=str(stylesheet_path))
		result = executable.transform_to_string(xdm_node=input_node)
	if result is None:
		raise RuntimeError(f"XSLT transform with {stylesheet_name} returned no result")
	return result


def render_einvoice_html(xml_string: str | bytes, lang: str = "de") -> str:
	"""Two-step KoSIT visualization: source XML → XR intermediate → HTML."""
	if isinstance(xml_string, bytes):
		xml_string = xml_string.decode("utf-8")

	xml_string = strip_xml_doctype(xml_string)
	einvoice_format = detect_einvoice_format(xml_string)
	if not einvoice_format:
		raise ValueError("Unsupported e-invoice format for visualization")

	xr_xml = transform_xml(xml_string, FORMAT_TO_XR_XSL[einvoice_format])
	# Language is read by functions.xsl via the $lang stylesheet parameter.
	# SaxonC applies parameters on the processor before compile for transform_to_string.
	# Defense in depth: strip again in case an intermediate ever carried a DOCTYPE.
	xr_xml = strip_xml_doctype(xr_xml)
	stylesheet_path = VISUALIZATION_DIR / "xrechnung-html.xsl"
	with PySaxonProcessor(license=False) as proc:
		xslt30_processor = proc.new_xslt30_processor()
		xslt30_processor.set_cwd(str(VISUALIZATION_DIR))
		xslt30_processor.set_parameter("lang", proc.make_string_value(lang))
		input_node = proc.parse_xml(xml_text=xr_xml)
		executable = xslt30_processor.compile_stylesheet(stylesheet_file=str(stylesheet_path))
		html = executable.transform_to_string(xdm_node=input_node)
	if html is None:
		raise RuntimeError("HTML visualization transform returned no result")
	return sanitize_viewer_html(_inject_frappe_viewer_styles(html))


def is_safe_viewer_url(url: str) -> bool:
	"""Allow only fragment, http(s), and mailto links in viewer HTML."""
	value = (url or "").strip()
	if not value or value.startswith("#"):
		return True
	lower = value.lower()
	return lower.startswith(("https://", "http://", "mailto:"))


def sanitize_viewer_html(html: str) -> str:
	"""Defense in depth for invoice-controlled markup inside the Desk iframe."""
	html = _ATTR_URL_RE.sub(_replace_unsafe_url_attr, html)
	html = _ON_ATTR_RE.sub(_replace_unsafe_handler_attr, html)
	return _inject_csp_meta(html)


def _replace_unsafe_url_attr(match: re.Match) -> str:
	if is_safe_viewer_url(match.group("url")):
		return match.group(0)
	return f"{match.group('prefix')}#{match.group('suffix')}"


def _replace_unsafe_handler_attr(match: re.Match) -> str:
	# Preserve trusted KoSIT tab + attachment download hooks only
	full = match.group(0)
	name = full.lstrip().split("=", 1)[0].strip().lower()
	if match.group("quote") is not None:
		value = (match.group("qvalue") or "").strip()
	else:
		value = (match.group("uvalue") or "").strip()
	if name == "onclick" and _SAFE_ONCLICK.match(value):
		return full
	return ""


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
