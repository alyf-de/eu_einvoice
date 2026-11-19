# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from frappe.tests import UnitTestCase

from eu_einvoice.xml_text import decode_xml_bytes


class TestDecodeXmlBytes(UnitTestCase):
	def test_decodes_iso_8859_1_declaration(self):
		payload = b'<?xml version="1.0" encoding="ISO-8859-1"?><root>\xe4</root>'
		self.assertEqual(decode_xml_bytes(payload), '<?xml version="1.0" encoding="ISO-8859-1"?><root>\xe4</root>')

	def test_strips_utf8_bom_via_utf8_sig(self):
		payload = b"\xef\xbb\xbf<?xml version='1.0' encoding='UTF-8'?><root>\xc3\xa4</root>"
		text = decode_xml_bytes(payload)
		self.assertFalse(text.startswith("\ufeff"))
		self.assertIn("\xe4", text)

	def test_defaults_to_utf8_without_declaration(self):
		payload = b"<root>\xc3\xa4</root>"
		self.assertEqual(decode_xml_bytes(payload), "<root>\xe4</root>")
