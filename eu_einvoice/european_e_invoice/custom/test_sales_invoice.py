from unittest.mock import patch

import frappe
from drafthorse.models.accounting import ApplicableTradeTax
from drafthorse.models.document import Document
from frappe.tests.utils import FrappeTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice import (
	EInvoiceGenerator,
	EInvoiceProfile,
	get_xml_attachment_file_base_name,
)


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


class TestHeaderTradeTaxDeduplication(FrappeTestCase):
	"""Verify that _merge_or_add_header_trade_tax collapses duplicates and sums
	distinct entries per (type_code, category_code, rate) combination
	(EN 16931 rule BR-30, CII-SR-045)."""

	def _make_generator(self):
		"""Return a bare EInvoiceGenerator instance with just a Document attached."""
		generator = EInvoiceGenerator.__new__(EInvoiceGenerator)
		generator.doc = Document()
		return generator

	def _make_tax(self, *, type_code="VAT", category="S", rate=19.0, basis=100.0, calc=19.0):
		tt = ApplicableTradeTax()
		tt.type_code = type_code
		tt.category_code = category
		tt.rate_applicable_percent = rate
		tt.basis_amount = basis
		tt.calculated_amount = calc
		return tt

	def _header_taxes(self, generator):
		return list(generator.doc.trade.settlement.trade_tax.children)

	def test_identical_duplicate_is_dropped(self):
		"""Two identical (S, 19%, basis=100, calc=19) tax rows collapse to one entry."""
		gen = self._make_generator()
		gen._merge_or_add_header_trade_tax(self._make_tax())
		gen._merge_or_add_header_trade_tax(self._make_tax())
		self.assertEqual(len(self._header_taxes(gen)), 1)

	def test_many_identical_duplicates_collapse_to_one(self):
		"""Reproduces the observed 13-row Item-Tax-Template pathology from ERPNext."""
		gen = self._make_generator()
		for _ in range(13):
			gen._merge_or_add_header_trade_tax(self._make_tax(basis=227.73, calc=43.27))
		taxes = self._header_taxes(gen)
		self.assertEqual(len(taxes), 1)
		self.assertAlmostEqual(taxes[0].basis_amount._value, 227.73)
		self.assertAlmostEqual(taxes[0].calculated_amount._value, 43.27)

	def test_distinct_rates_kept_separate(self):
		"""(S, 19%) and (S, 7%) must remain two distinct entries."""
		gen = self._make_generator()
		gen._merge_or_add_header_trade_tax(self._make_tax(rate=19.0, basis=100.0, calc=19.0))
		gen._merge_or_add_header_trade_tax(self._make_tax(rate=7.0, basis=50.0, calc=3.5))
		self.assertEqual(len(self._header_taxes(gen)), 2)

	def test_distinct_categories_kept_separate(self):
		"""(S, 19%) and (AE, 19%) must remain two distinct entries."""
		gen = self._make_generator()
		gen._merge_or_add_header_trade_tax(self._make_tax(category="S"))
		gen._merge_or_add_header_trade_tax(self._make_tax(category="AE"))
		self.assertEqual(len(self._header_taxes(gen)), 2)

	def test_distinct_type_codes_kept_separate(self):
		"""(VAT, S, 19%) and (SUR, S, 19%) must remain two distinct entries."""
		gen = self._make_generator()
		gen._merge_or_add_header_trade_tax(self._make_tax(type_code="VAT"))
		gen._merge_or_add_header_trade_tax(self._make_tax(type_code="SUR"))
		self.assertEqual(len(self._header_taxes(gen)), 2)

	def test_distinct_amounts_same_key_are_summed(self):
		"""Two (S, 19%) entries with different basis/calc are summed into one."""
		gen = self._make_generator()
		gen._merge_or_add_header_trade_tax(self._make_tax(basis=100.0, calc=19.0))
		gen._merge_or_add_header_trade_tax(self._make_tax(basis=50.0, calc=9.5))
		taxes = self._header_taxes(gen)
		self.assertEqual(len(taxes), 1)
		self.assertAlmostEqual(taxes[0].basis_amount._value, 150.0)
		self.assertAlmostEqual(taxes[0].calculated_amount._value, 28.5)

	def test_single_entry_added_unchanged(self):
		"""A single call is passed through without modification."""
		gen = self._make_generator()
		gen._merge_or_add_header_trade_tax(self._make_tax(basis=100.0, calc=19.0))
		taxes = self._header_taxes(gen)
		self.assertEqual(len(taxes), 1)
		self.assertAlmostEqual(taxes[0].basis_amount._value, 100.0)
