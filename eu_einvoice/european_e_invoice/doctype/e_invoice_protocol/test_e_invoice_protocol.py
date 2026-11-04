# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

# Permission tests live in european_e_invoice.test_protocol (TestEInvoiceProtocolPermissions)
# to avoid DocType IntegrationTestCase fixture loading (Fiscal Year) on this suite.
from frappe.tests import IntegrationTestCase

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestEInvoiceProtocol(IntegrationTestCase):
	pass
