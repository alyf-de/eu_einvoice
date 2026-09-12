from unittest.mock import MagicMock, patch

import frappe
from drafthorse.models.document import Document
from frappe.tests.utils import FrappeTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice import (
	EInvoiceGenerator,
	duty_tax_fee_category_codes,
	get_item_rate,
	get_xml_attachment_file_base_name,
)
from eu_einvoice.utils import EInvoiceProfile


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

	def test_all_matching_rows_not_applicable_returns_zero(self):
		"""The template says no invoice tax applies, so the line is 0% - not the fallback rate."""
		template = self._template([{"tax_type": "VAT 19%", "tax_rate": 0, "not_applicable": 1}])
		meta = MagicMock()
		meta.get_field.return_value = MagicMock()
		taxes = [frappe._dict(account_head="VAT 19%", charge_type="On Net Total", rate=19)]
		with (
			patch("frappe.get_cached_doc", return_value=template),
			patch("frappe.get_meta", return_value=meta),
		):
			self.assertEqual(get_item_rate("Exempt", taxes), 0)

	def test_all_matching_rows_zero_rated_returns_zero_without_not_applicable_field(self):
		template = self._template([{"tax_type": "VAT 19%", "tax_rate": 0}])
		meta = MagicMock()
		meta.get_field.return_value = None
		taxes = [frappe._dict(account_head="VAT 19%", charge_type="On Net Total", rate=19)]
		with (
			patch("frappe.get_cached_doc", return_value=template),
			patch("frappe.get_meta", return_value=meta),
		):
			self.assertEqual(get_item_rate("Exempt", taxes), 0)

	def test_falls_back_to_single_tax_row_when_template_does_not_match(self):
		template = self._template([{"tax_type": "VAT 5%", "tax_rate": 5}])
		meta = MagicMock()
		meta.get_field.return_value = None
		taxes = [frappe._dict(account_head="VAT 19%", charge_type="On Net Total", rate=19)]
		with (
			patch("frappe.get_cached_doc", return_value=template),
			patch("frappe.get_meta", return_value=meta),
		):
			self.assertEqual(get_item_rate("5 %", taxes), 19)


class TestApplicableTradeTaxes(FrappeTestCase):
	def test_multi_tax_invoice_groups_and_basis_amounts(self):
		"""Unused tax rows are dropped, the basis comes from net_amount before any fallback."""
		invoice = frappe._dict(
			net_total=1000,
			tax_category=None,
			taxes_and_charges=None,
			precision=lambda fieldname: 2,
			taxes=[
				# basis from the ERPNext tax row net_amount
				frappe._dict(
					account_head="_Test VAT 19",
					charge_type="On Net Total",
					rate=19,
					tax_amount=19,
					net_amount=100,
				),
				# basis from the custom field of older ERPNext versions
				frappe._dict(
					account_head="_Test VAT 7",
					charge_type="On Net Total",
					rate=7,
					tax_amount=7,
					custom_net_amount=100,
				),
				# net_amount exists as a field, but is empty, so it is calculated as well
				frappe._dict(
					account_head="_Test VAT 10",
					charge_type="On Net Total",
					rate=10,
					tax_amount=10,
					net_amount=0,
				),
				# no net amount known, so it is calculated from tax amount and rate
				frappe._dict(account_head="_Test VAT 5", charge_type="On Net Total", rate=5, tax_amount=0.5),
				# no tax amount, but a line item uses this rate (net amount 0), so it is kept
				frappe._dict(account_head="_Test VAT 3", charge_type="On Net Total", rate=3, tax_amount=0),
				# no tax amount and no line item uses this rate, so there is nothing to declare
				frappe._dict(account_head="_Test VAT 2", charge_type="On Net Total", rate=2, tax_amount=0),
			],
		)
		generator = EInvoiceGenerator(EInvoiceProfile.EN16931, invoice, None, None)
		generator.doc = Document()
		generator.item_tax_rates = {19, 7, 10, 5, 3}

		with patch.object(duty_tax_fee_category_codes, "get", return_value="S"):
			self.assertTrue(generator._add_taxes_and_charges())

		trade_taxes = generator.doc.trade.settlement.trade_tax.children
		self.assertEqual([t.rate_applicable_percent._value for t in trade_taxes], [19, 7, 10, 5, 3])
		self.assertEqual([t.basis_amount._value for t in trade_taxes], [100, 100, 100, 10, 0])


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
