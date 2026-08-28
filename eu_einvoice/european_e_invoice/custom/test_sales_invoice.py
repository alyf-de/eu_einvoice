from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice import (
	get_item_rate,
	get_xml_attachment_file_base_name,
)


class TestGetItemRate(FrappeTestCase):
	def _taxes(self):
		return [
			frappe._dict(account_head="VAT 19%", charge_type="On Net Total", rate=0),
			frappe._dict(account_head="VAT 7%", charge_type="On Net Total", rate=0),
		]

	def _template(self, rows):
		doc = MagicMock()
		doc.taxes = [frappe._dict(row) for row in rows]
		return doc

	def test_skips_zero_rate_without_not_applicable_field(self):
		template = self._template(
			[
				{"tax_type": "VAT 19%", "tax_rate": 0},
				{"tax_type": "VAT 7%", "tax_rate": 7},
			]
		)
		meta = MagicMock()
		meta.get_field.return_value = None
		with (
			patch("frappe.get_cached_doc", return_value=template),
			patch("frappe.get_meta", return_value=meta),
		):
			self.assertEqual(get_item_rate("7 %", self._taxes()), 7)

	def test_skips_not_applicable_row_when_field_exists(self):
		template = self._template(
			[
				{"tax_type": "VAT 19%", "tax_rate": 0, "not_applicable": 1},
				{"tax_type": "VAT 7%", "tax_rate": 7, "not_applicable": 0},
			]
		)
		meta = MagicMock()
		meta.get_field.return_value = MagicMock()
		with (
			patch("frappe.get_cached_doc", return_value=template),
			patch("frappe.get_meta", return_value=meta),
		):
			self.assertEqual(get_item_rate("7 %", self._taxes()), 7)


class TestXmlAttachmentNaming(FrappeTestCase):
	def test_auto_name_format_from_e_invoice_settings(self):
		doc = frappe._dict(name="SINV-00001", po_no="PO-42", doctype="Sales Invoice")
		field = "auto_name_format_for_xml_file"
		pattern = "XMLSTEM.-.{po_no}"
		real_gsv = frappe.get_single_value

		def get_single_value(doctype, fname, cache=True):
			if doctype == "E Invoice Settings" and fname == field:
				return pattern
			return real_gsv(doctype, fname, cache=cache)

		with patch.object(frappe, "get_single_value", side_effect=get_single_value):
			self.assertEqual(get_xml_attachment_file_base_name(doc), "XMLSTEM-PO-42")

	def test_empty_or_whitespace_uses_doc_name(self):
		doc = frappe._dict(name="SINV/00001", doctype="Sales Invoice")
		self.assertEqual(get_xml_attachment_file_base_name(doc, pattern=""), "SINV_00001")
		self.assertEqual(get_xml_attachment_file_base_name(doc, pattern="   "), "SINV_00001")

	def test_dot_separated_pattern_with_field(self):
		doc = frappe._dict(name="SINV-00001", po_no="PO-1", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="EXAMPLE.-.{po_no}")
		self.assertEqual(base, "EXAMPLE-PO-1")

	def test_dot_separated_inv_field_end(self):
		doc = frappe._dict(name="SINV-00001", po_no="X-9", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="INV.-.{po_no}.-.END")
		self.assertEqual(base, "INV-X-9-END")

	def test_leading_hashes_stripped_per_part_avoids_series_counter(self):
		doc = frappe._dict(name="SINV-00001", po_no="PO-1", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="EXAMPLE.-.{po_no}.#####")
		self.assertEqual(base, "EXAMPLE-PO-1")

	def test_hash_in_literal_sanitized_like_file_utils(self):
		doc = frappe._dict(name="SINV-00001", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="INV#X")
		self.assertEqual(base, "INV_X")

	def test_only_hash_segments_falls_back_to_doc_name(self):
		doc = frappe._dict(name="SINV-00001", doctype="Sales Invoice")
		self.assertEqual(get_xml_attachment_file_base_name(doc, pattern="#####"), "SINV-00001")

	def test_slash_in_resolved_base_is_sanitized(self):
		doc = frappe._dict(name="SINV-00001", po_no="A/B", doctype="Sales Invoice")
		base = get_xml_attachment_file_base_name(doc, pattern="{po_no}")
		self.assertEqual(base, "A_B")
