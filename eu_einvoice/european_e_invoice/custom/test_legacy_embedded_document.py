# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

from unittest.mock import patch

from frappe.tests import UnitTestCase

from eu_einvoice.european_e_invoice.custom.legacy_embed_scenarios import load_embed_attachment_scenarios
from eu_einvoice.european_e_invoice.custom.legacy_embed_test_helpers import (
	assert_embed_attachment_result,
	make_embed_generator,
	make_sales_invoice_doc,
	mock_file_doc,
)


class UnitTestEmbedAttachment(UnitTestCase):
	def test_embed_attachment_scenarios(self):
		for scenario in load_embed_attachment_scenarios():
			with self.subTest(scenario=scenario.id):
				invoice = make_sales_invoice_doc(einvoice_embedded_document=scenario.field_url)
				generator = make_embed_generator(invoice)

				mock_doc = mock_file_doc(scenario.mock_file) if scenario.mock_file else None
				with patch(
					"eu_einvoice.european_e_invoice.custom.sales_invoice.find_file_by_url",
					return_value=mock_doc,
				) as find_mock:
					generator._embed_attachment()
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
