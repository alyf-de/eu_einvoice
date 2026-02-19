from frappe.tests.utils import FrappeTestCase

from eu_einvoice.switzerland import validate_swiss_vat


class TestSwitzerland(FrappeTestCase):
	def test_validate_swiss_vat(self):
		self.assertTrue(validate_swiss_vat("CHE-116.281.710 MWST"))  # True (Nestlé)
		self.assertTrue(validate_swiss_vat("CHE-101.654.423 TVA"))  # True (Swisscom)
		self.assertFalse(validate_swiss_vat("CHE123456789"))  # False (Invalid checksum)
