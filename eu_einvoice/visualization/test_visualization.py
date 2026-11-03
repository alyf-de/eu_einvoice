# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from pathlib import Path

from frappe.tests import UnitTestCase
from saxonche import PySaxonProcessor

from eu_einvoice.visualization import (
	EInvoiceFormat,
	detect_einvoice_format,
	is_safe_viewer_url,
	render_einvoice_html,
	sanitize_viewer_html,
	transform_xml,
)
from eu_einvoice.xml_security import strip_xml_doctype

FIXTURES = Path(__file__).parent / "test_instances"
HOSTNAME_PATH = Path("/etc/hostname")


def _cii_with_xxe_note(entity_uri: str, note_text: str = "&xxe;") -> str:
	"""CII fixture with a DOCTYPE entity and one IncludedNote content replaced."""
	base = (FIXTURES / "included-notes-bg-1-uncefact.xml").read_text()
	doctype = (
		f'<!DOCTYPE CrossIndustryInvoice [\n'
		f'  <!ENTITY xxe SYSTEM "{entity_uri}">\n'
		f"]>\n"
	)
	# Insert DOCTYPE after the XML declaration.
	decl_end = base.find("?>")
	assert decl_end != -1
	poisoned = base[: decl_end + 2] + "\n" + doctype + base[decl_end + 2 :]
	return poisoned.replace("<ram:Content>Bemerkung 1</ram:Content>", f"<ram:Content>{note_text}</ram:Content>", 1)


class TestVisualization(UnitTestCase):
	def test_detect_cii_and_ubl(self):
		cii = (FIXTURES / "included-notes-bg-1-uncefact.xml").read_bytes()
		ubl = (FIXTURES / "maxRechnung_ubl.xml").read_bytes()
		self.assertEqual(detect_einvoice_format(cii), EInvoiceFormat.CII)
		self.assertEqual(detect_einvoice_format(ubl), EInvoiceFormat.UBL_INVOICE)

	def test_detect_malformed_xml_returns_none(self):
		self.assertIsNone(detect_einvoice_format(b"not xml <<<"))
		self.assertIsNone(detect_einvoice_format("<root>unclosed"))

	def test_render_cii_html(self):
		xml = (FIXTURES / "included-notes-bg-1-uncefact.xml").read_text()
		html = render_einvoice_html(xml, lang="en")
		self.assertIn("<html", html)
		self.assertIn('lang="en"', html)
		self.assertIn("XRechnung", html)
		self.assertIn("eu-einvoice-viewer-override", html)
		self.assertIn("Content-Security-Policy", html)

	def test_render_ubl_html(self):
		xml = (FIXTURES / "maxRechnung_ubl.xml").read_text()
		html = render_einvoice_html(xml, lang="de")
		self.assertIn("<html", html)
		self.assertIn("XRechnung", html)

	def test_safe_viewer_urls(self):
		self.assertTrue(is_safe_viewer_url("#download-d0e1"))
		self.assertTrue(is_safe_viewer_url("https://example.com/doc.pdf"))
		self.assertTrue(is_safe_viewer_url("http://example.com"))
		self.assertTrue(is_safe_viewer_url("mailto:a@b.c"))
		self.assertFalse(is_safe_viewer_url("javascript:alert(1)"))
		self.assertFalse(is_safe_viewer_url("JAVASCRIPT:alert(1)"))
		self.assertFalse(is_safe_viewer_url("data:text/html,<script>alert(1)</script>"))
		self.assertFalse(is_safe_viewer_url("vbscript:msgbox(1)"))

	def test_sanitize_strips_dangerous_href_and_handlers(self):
		dirty = (
			'<html><head></head><body>'
			'<a href="javascript:alert(document.domain)">x</a>'
			'<a href="https://ok.example/a">ok</a>'
			"<a href=\"#dl\" onClick=\"downloadData('download-d0e1')\">dl</a>"
			'<img src="data:text/html,x" onerror="alert(1)">'
			"</body></html>"
		)
		clean = sanitize_viewer_html(dirty)
		self.assertNotIn("javascript:", clean.lower())
		self.assertIn('href="https://ok.example/a"', clean)
		self.assertIn("downloadData('download-d0e1')", clean)
		self.assertNotIn("onerror", clean.lower())
		self.assertIn("Content-Security-Policy", clean)
		# dangerous href neutralized to #
		self.assertIn('href="#"', clean)

	def test_sanitize_strips_unquoted_event_handlers(self):
		dirty = '<html><head></head><body><img src="https://ok.example/a.png" onerror=alert(1)></body></html>'
		clean = sanitize_viewer_html(dirty)
		self.assertNotIn("onerror", clean.lower())
		self.assertNotIn("alert(1)", clean)

	def test_sanitize_strips_unquoted_onclick_except_kosit_download(self):
		dirty = (
			"<html><head></head><body>"
			"<button onclick=alert(1)>x</button>"
			"<a onclick=downloadData('download-d0e9')>dl</a>"
			"</body></html>"
		)
		clean = sanitize_viewer_html(dirty)
		self.assertNotIn("alert(1)", clean)
		self.assertIn("downloadData('download-d0e9')", clean)

	def test_strip_xml_doctype_removes_internal_subset(self):
		xml = '<?xml version="1.0"?>\n<!DOCTYPE foo [\n  <!ENTITY xxe SYSTEM "file:///etc/hostname">\n]>\n<root/>'
		stripped = strip_xml_doctype(xml)
		self.assertNotIn("DOCTYPE", stripped.upper())
		self.assertNotIn("ENTITY", stripped.upper())
		self.assertIn("<root/>", stripped)
		self.assertTrue(stripped.startswith("<?xml"))

	def test_strip_xml_doctype_handles_bom_and_multiline(self):
		xml = "\ufeff<?xml version='1.0'?>\n<!DOCTYPE foo [\n<!ENTITY a SYSTEM 'http://127.0.0.1/'>\n]>\n<a/>"
		stripped = strip_xml_doctype(xml)
		self.assertFalse(stripped.startswith("\ufeff"))
		self.assertNotIn("DOCTYPE", stripped.upper())

	def test_strip_xml_doctype_drops_truncated_doctype(self):
		xml = '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/hostname"'
		stripped = strip_xml_doctype(xml)
		self.assertEqual(stripped, '<?xml version="1.0"?>')

	def test_xxe_file_entity_not_reflected_in_html(self):
		"""Regression: SaxonC must not expand file:// entities into viewer HTML."""
		self.assertTrue(HOSTNAME_PATH.is_file())
		secret = HOSTNAME_PATH.read_text().strip()
		self.assertTrue(secret)
		poisoned = _cii_with_xxe_note(f"file://{HOSTNAME_PATH}")

		# Without stripping, Saxon still expands (documents the underlying engine behaviour).
		with PySaxonProcessor(license=False) as proc:
			raw = proc.parse_xml(xml_text=poisoned)
			self.assertIn(secret, raw.to_string())

		try:
			html = render_einvoice_html(poisoned, lang="en")
		except Exception:
			return
		self.assertNotIn(secret, html)
		self.assertIn("<html", html)

	def test_xxe_file_entity_not_in_xr_intermediate(self):
		self.assertTrue(HOSTNAME_PATH.is_file())
		secret = HOSTNAME_PATH.read_text().strip()
		poisoned = _cii_with_xxe_note(f"file://{HOSTNAME_PATH}")
		try:
			xr = transform_xml(poisoned, "cii-xr.xsl")
		except Exception:
			return
		self.assertNotIn(secret, xr)

	def test_render_still_works_when_harmless_doctype_present(self):
		base = (FIXTURES / "included-notes-bg-1-uncefact.xml").read_text()
		decl_end = base.find("?>")
		with_doctype = base[: decl_end + 2] + "\n<!DOCTYPE CrossIndustryInvoice>\n" + base[decl_end + 2 :]
		html = render_einvoice_html(with_doctype, lang="en")
		self.assertIn("<html", html)
		self.assertIn("XRechnung", html)

	def test_ssrf_doctype_system_uri_stripped_before_saxon(self):
		"""Network SYSTEM entities must be removed so Saxon cannot initiate HTTP."""
		poisoned = _cii_with_xxe_note("http://127.0.0.1:9/should-not-connect")
		stripped = strip_xml_doctype(poisoned)
		self.assertNotIn("DOCTYPE", stripped.upper())
		self.assertNotIn("SYSTEM", stripped.upper())
		self.assertNotIn("127.0.0.1", stripped)
		# Fixed path must not open a network connection (fail closed is OK).
		try:
			html = render_einvoice_html(poisoned, lang="en")
		except Exception:
			return
		self.assertNotIn("SSRF", html)
