# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

from unittest.mock import patch

from frappe.core.doctype.file.utils import find_file_by_url
from frappe.tests import IntegrationTestCase, UnitTestCase

from eu_einvoice.european_e_invoice.custom.legacy_embed_scenarios import load_embed_attachment_scenarios
from eu_einvoice.european_e_invoice.custom.legacy_embed_test_helpers import (
	assert_embed_attachment_result,
	make_embed_generator,
	make_sales_invoice_doc,
	mock_file_doc,
)
from eu_einvoice.european_e_invoice.custom.sales_invoice import as_base_64, get_legacy_embed_attachment
from eu_einvoice.tests.helpers import (
	build_einvoice_generator,
	create_embed_test_sales_invoice,
	delete_embed_test_annex_file,
	delete_embed_test_sales_invoice,
)


class UnitTestEmbedAttachment(UnitTestCase):
	def test_get_legacy_embed_attachment(self):
		for field_url, expected in (
			("", []),
			("/files/legacy-annex.png", ["/files/legacy-annex.png"]),
		):
			with self.subTest(field_url=field_url):
				invoice = make_sales_invoice_doc(einvoice_embedded_document=field_url)
				self.assertEqual(get_legacy_embed_attachment(invoice), expected)

	def test_embed_attachments_scenarios(self):
		for scenario in load_embed_attachment_scenarios():
			with self.subTest(scenario=scenario.id):
				generator = make_embed_generator(make_sales_invoice_doc())
				attachments = [scenario.field_url] if scenario.field_url else []

				mock_doc = mock_file_doc(scenario.mock_file) if scenario.mock_file else None
				with patch(
					"eu_einvoice.european_e_invoice.custom.sales_invoice.find_file_by_url",
					return_value=mock_doc,
				) as find_mock:
					generator._embed_attachments(attachments)
					if scenario.mock_file:
						find_mock.assert_called_once_with(scenario.field_url)
					else:
						find_mock.assert_not_called()

				mock_content = scenario.mock_file.content if scenario.mock_file else None
				assert_embed_attachment_result(
					generator,
					scenario.expect,
					mock_content=mock_content,
				)


class IntegrationTestCreateEinvoiceEmbed(IntegrationTestCase):
	def test_create_einvoice_embeds_legacy_attachment(self):
		sales_invoice, annex_file = create_embed_test_sales_invoice()
		self.addCleanup(delete_embed_test_sales_invoice, sales_invoice.name)
		self.addCleanup(delete_embed_test_annex_file, annex_file.name)

		generator = build_einvoice_generator(sales_invoice)
		generator.create_einvoice()

		resolved_file = find_file_by_url(annex_file.file_url)
		refs = generator.doc.trade.agreement.additional_references.children
		self.assertEqual(len(refs), 1)

		ref = refs[0]
		self.assertEqual(str(ref.type_code), "916")
		self.assertEqual(ref.issuer_assigned_id._text, resolved_file.name)

		attached_object = ref.attached_object
		self.assertEqual(attached_object._mime_code, "image/png")
		self.assertEqual(attached_object._filename, annex_file.file_name)
		self.assertEqual(attached_object._text, as_base_64(resolved_file.get_content()))
