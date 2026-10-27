from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice import SalesInvoiceGenerator
from eu_einvoice.european_e_invoice.einvoice import attach_xml_to_pdf, get_einvoice, get_generator_class


class TestGeneratorRegistry(IntegrationTestCase):
	def test_sales_invoice_is_registered(self):
		self.assertIs(get_generator_class("Sales Invoice"), SalesInvoiceGenerator)

	def test_unregistered_doctype(self):
		self.assertIsNone(get_generator_class("ToDo"))

	def test_last_registered_app_wins(self):
		hooks = {
			"Sales Invoice": [
				"eu_einvoice.european_e_invoice.einvoice.EInvoiceGenerator",
				"eu_einvoice.european_e_invoice.custom.sales_invoice.SalesInvoiceGenerator",
			]
		}
		with patch("frappe.get_hooks", return_value=hooks):
			self.assertIs(get_generator_class("Sales Invoice"), SalesInvoiceGenerator)

	def test_get_einvoice_rejects_unregistered_doctype(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Not an invoice"})
		self.assertRaises(frappe.ValidationError, get_einvoice, todo)

	def test_attach_xml_to_pdf_keeps_pdf_of_unregistered_doctype(self):
		pdf_data = b"%PDF-1.4 not an invoice"
		self.assertIs(attach_xml_to_pdf("TODO-0001", pdf_data, doctype="ToDo"), pdf_data)
