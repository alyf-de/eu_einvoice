from frappe.tests.utils import FrappeTestCase

from eu_einvoice.switzerland import is_valid_swiss_vat_id


class TestSwitzerland(FrappeTestCase):
	def test_validate_swiss_vat(self):
		self.assertTrue(is_valid_swiss_vat_id("CHE-116.281.710 MWST"))  # True (Nestlé)
		self.assertTrue(is_valid_swiss_vat_id("CHE-101.654.423 TVA"))  # True (Swisscom)
		self.assertFalse(is_valid_swiss_vat_id("CHE123456789"))  # False (Invalid checksum)
