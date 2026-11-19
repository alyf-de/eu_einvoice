# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from pathlib import Path

from frappe.tests import UnitTestCase
from saxonche import PySaxonProcessor

from eu_einvoice.saxon import UnsafeXMLError
from eu_einvoice.visualization import (
	EInvoiceFormat,
	detect_einvoice_format,
	render_einvoice_html,
	transform_xml,
)

FIXTURES = Path(__file__).parent / "test_instances"
HOSTNAME_PATH = Path("/etc/hostname")


def _cii_with_xxe_note(entity_uri: str, note_text: str = "&xxe;") -> str:
	"""CII fixture with a DOCTYPE entity and one IncludedNote content replaced."""
	base = (FIXTURES / "included-notes-bg-1-uncefact.xml").read_text()
	doctype = f'<!DOCTYPE CrossIndustryInvoice [\n  <!ENTITY xxe SYSTEM "{entity_uri}">\n]>\n'
	# Insert DOCTYPE after the XML declaration.
	decl_end = base.find("?>")
	assert decl_end != -1
	poisoned = base[: decl_end + 2] + "\n" + doctype + base[decl_end + 2 :]
	return poisoned.replace(
		"<ram:Content>Bemerkung 1</ram:Content>", f"<ram:Content>{note_text}</ram:Content>", 1
	)


class TestVisualization(UnitTestCase):
	def test_detect_cii_and_ubl(self):
		cii = (FIXTURES / "included-notes-bg-1-uncefact.xml").read_bytes()
		ubl = (FIXTURES / "maxRechnung_ubl.xml").read_bytes()
		self.assertEqual(detect_einvoice_format(cii), EInvoiceFormat.CII)
		self.assertEqual(detect_einvoice_format(ubl), EInvoiceFormat.UBL_INVOICE)

	def test_detect_cii_requires_root_and_namespace(self):
		# Root alone must not be treated as CII (empty docs would pass EN 16931).
		self.assertIsNone(detect_einvoice_format(b"<CrossIndustryInvoice/>"))
		self.assertIsNone(detect_einvoice_format(b'<CrossIndustryInvoice xmlns="urn:example:not-cii"/>'))
		self.assertIsNone(
			detect_einvoice_format(
				b'<Other xmlns="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"/>'
			)
		)
		self.assertEqual(
			detect_einvoice_format(
				b'<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"/>'
			),
			EInvoiceFormat.CII,
		)

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

	def test_render_keeps_tab_onclick_handlers(self):
		xml = (FIXTURES / "included-notes-bg-1-uncefact.xml").read_text()
		html = render_einvoice_html(xml, lang="en")
		self.assertIn('id="menueDetails"', html)
		self.assertRegex(html, r'id="menueDetails"[^>]*onclick="show\(this\);?"')
		self.assertRegex(html, r'id="menueAnlagen"[^>]*onclick="show\(this\);?"')

	def test_render_preserves_invoice_text_that_looks_like_attributes(self):
		"""Regression: regex sanitiser used to rewrite note text such as src='x'."""
		xml = (FIXTURES / "included-notes-bg-1-uncefact.xml").read_text()
		xml = xml.replace(
			"<ram:Content>Bemerkung 1</ram:Content>",
			"<ram:Content>Lieferung online=ja, Tarif only=5 src='x'</ram:Content>",
			1,
		)
		html = render_einvoice_html(xml, lang="en")
		self.assertIn("Lieferung online=ja, Tarif only=5 src='x'", html)
		self.assertIn("Content-Security-Policy", html)

	def test_xxe_file_entity_rejected_by_saxon(self):
		"""Regression: DOCTYPE XXE must be rejected via Saxon DISALLOW_DOCTYPE."""
		self.assertTrue(HOSTNAME_PATH.is_file())
		secret = HOSTNAME_PATH.read_text().strip()
		self.assertTrue(secret)
		poisoned = _cii_with_xxe_note(f"file://{HOSTNAME_PATH}")

		# Without DISALLOW_DOCTYPE, Saxon still expands (documents engine behaviour).
		with PySaxonProcessor(license=False) as proc:
			raw = proc.parse_xml(xml_text=poisoned)
			self.assertIn(secret, raw.to_string())

		with self.assertRaises(UnsafeXMLError):
			render_einvoice_html(poisoned, lang="en")

	def test_xxe_file_entity_rejected_in_transform(self):
		poisoned = _cii_with_xxe_note(f"file://{HOSTNAME_PATH}")
		with self.assertRaises(UnsafeXMLError):
			transform_xml(poisoned, "cii-xr.xsl")

	def test_render_rejects_harmless_doctype(self):
		base = (FIXTURES / "included-notes-bg-1-uncefact.xml").read_text()
		decl_end = base.find("?>")
		with_doctype = base[: decl_end + 2] + "\n<!DOCTYPE CrossIndustryInvoice>\n" + base[decl_end + 2 :]
		with self.assertRaises(UnsafeXMLError):
			render_einvoice_html(with_doctype, lang="en")

	def test_ssrf_doctype_system_uri_rejected(self):
		"""Network SYSTEM entities must never reach a DOCTYPE-enabled parse."""
		poisoned = _cii_with_xxe_note("http://127.0.0.1:9/should-not-connect")
		with self.assertRaises(UnsafeXMLError):
			render_einvoice_html(poisoned, lang="en")
