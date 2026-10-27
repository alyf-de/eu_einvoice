from unittest.mock import patch

import frappe
from drafthorse.models.document import IncludedNote
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from frappe.tests import IntegrationTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice import SalesInvoiceGenerator
from eu_einvoice.european_e_invoice.einvoice import attach_xml_to_pdf, get_einvoice, get_generator_class
from eu_einvoice.utils import EInvoiceProfile

HOOK_PATH = "eu_einvoice.european_e_invoice.test_einvoice"


def before_einvoice_generation(doc, method=None):
	"""Like doc_events in other apps: change the invoice before it is read."""
	doc.po_no = "PO-FROM-BEFORE-HOOK"


def after_einvoice_generation(doc, method=None, einvoice_doc=None):
	"""Like doc_events in other apps: patch the drafthorse document before it is serialized."""
	note = IncludedNote(subject_code="AAI")
	note.content = f"Added by hook for {doc.name}"
	einvoice_doc.header.notes.add(note)


class TestEInvoiceHooks(IntegrationTestCase):
	def test_doc_events_before_and_after_generation(self):
		invoice = create_sales_invoice(do_not_save=True)
		invoice.einvoice_profile = EInvoiceProfile.EN16931.value
		invoice.insert()

		doc_events = {
			"Sales Invoice": {
				"before_einvoice_generation": [f"{HOOK_PATH}.before_einvoice_generation"],
				"after_einvoice_generation": [f"{HOOK_PATH}.after_einvoice_generation"],
			}
		}
		with patch("frappe.get_doc_hooks", return_value=doc_events):
			xml = get_einvoice(invoice).decode()

		self.assertIn("PO-FROM-BEFORE-HOOK", xml)
		self.assertIn(f"Added by hook for {invoice.name}", xml)


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
