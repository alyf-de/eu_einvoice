# Copyright (c) 2024, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestEInvoiceImport(IntegrationTestCase):
	def test_default_company_without_buyer_id(self):
		frappe.defaults.set_user_default("company", "_Test Company")
		self.addCleanup(frappe.defaults.clear_user_default, "company")

		doc = frappe.new_doc("E Invoice Import")
		doc.buyer_name = "Unknown Buyer GmbH"

		doc.guess_company()
		doc.guess_company_and_supplier()

		self.assertEqual(doc.company, "_Test Company")
