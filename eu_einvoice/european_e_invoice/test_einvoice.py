from unittest.mock import MagicMock, patch

import frappe
from drafthorse.models.document import Document, IncludedNote
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from frappe.tests.utils import FrappeTestCase

from eu_einvoice.european_e_invoice.custom.sales_invoice import SalesInvoiceGenerator
from eu_einvoice.european_e_invoice.einvoice import (
	EInvoiceGenerator,
	TradeParty,
	attach_xml_to_pdf,
	duty_tax_fee_category_codes,
	get_einvoice,
	get_generator_class,
)
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


class TestEInvoiceHooks(FrappeTestCase):
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


class TestPartyTaxRegistration(FrappeTestCase):
	def test_category_o_suppresses_only_vat_ids(self):
		for category in ("O", "S"):
			for scheme in ("VA", "FC"):
				with self.subTest(category=category, scheme=scheme):
					invoice = MagicMock(items=[object()], tax_category=None, taxes_and_charges=None)
					generator = EInvoiceGenerator(invoice, EInvoiceProfile.EN16931)
					generator.tax_template_doctype = "Custom Tax Template"
					doc = Document()
					party = TradeParty(
						name="Test Party",
						tax_registration=(scheme, "123456789"),
						email="test@example.com",
					)
					with (
						patch.object(generator, "get_line_lookup", return_value=[]),
						patch.object(duty_tax_fee_category_codes, "get", return_value=category) as get_code,
					):
						for target in (doc.trade.agreement.seller, doc.trade.agreement.buyer):
							generator._write_party(target, party)
							registrations = target.tax_registrations.children
							if category == "O" and scheme == "VA":
								self.assertEqual(registrations, [])
							else:
								self.assertEqual(len(registrations), 1)
								self.assertEqual(registrations[0].id._text, "123456789")
								self.assertEqual(registrations[0].id._scheme_id, scheme)
							self.assertEqual(target.contact.email.address._text, party.email)
						if scheme == "VA":
							get_code.assert_called_once_with(
								[("Tax Category", None), ("Custom Tax Template", None)]
							)


class TestGeneratorRegistry(FrappeTestCase):
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
