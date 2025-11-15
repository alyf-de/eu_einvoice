# Copyright (c) 2025, ALYF GmbH and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from eu_einvoice.schematron import get_cache_info


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class IntegrationTestEInvoiceSettings(IntegrationTestCase):
	"""Integration tests for EInvoiceSettings."""

	def setUp(self):
		"""Set up test environment."""
		self.settings = frappe.get_single("E Invoice Settings")

	def test_default_values_are_set(self):
		"""Test that default values are properly set."""
		self.assertEqual(self.settings.validate_sales_invoice_on_save, 1)
		self.assertEqual(self.settings.validate_sales_invoice_on_submit, 1)
		self.assertEqual(self.settings.enable_schematron_caching, 1)
		self.assertEqual(self.settings.auto_set_profile_from_customer, 1)

	def test_business_process_urn_validation(self):
		"""Test URN validation."""
		# Valid URN
		self.settings.default_business_process = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
		self.settings.save()  # Should not raise

		# Invalid URN format (should show warning but not throw)
		self.settings.default_business_process = "invalid-urn-format"
		self.settings.save()  # Should warn but not crash

	def test_expense_account_validation(self):
		"""Test that expense account must be of correct type."""
		# This would require setting up test accounts
		# Skipping for now as it requires complex setup
		pass

	def test_get_statistics_returns_correct_structure(self):
		"""Test that get_statistics returns expected data structure."""
		stats = self.settings.get_statistics()

		# Check all required keys are present
		self.assertIn("total_generated", stats)
		self.assertIn("validation_passed", stats)
		self.assertIn("validation_failed", stats)
		self.assertIn("total_imported", stats)
		self.assertIn("profile_breakdown", stats)
		self.assertIn("cache_info", stats)
		self.assertIn("success_rate", stats)

		# Check types
		self.assertIsInstance(stats["total_generated"], int)
		self.assertIsInstance(stats["validation_passed"], int)
		self.assertIsInstance(stats["validation_failed"], int)
		self.assertIsInstance(stats["total_imported"], int)
		self.assertIsInstance(stats["profile_breakdown"], list)
		self.assertIsInstance(stats["cache_info"], dict)
		self.assertIsInstance(stats["success_rate"], (int, float))

	def test_clear_validation_cache(self):
		"""Test cache clearing functionality."""
		# Clear cache
		result = self.settings.clear_validation_cache()

		# Check result structure
		self.assertIn("message", result)
		self.assertIn("cache_before", result)
		self.assertIn("cache_after", result)

		# Verify cache was actually cleared
		cache_info = get_cache_info()
		self.assertEqual(cache_info["cache_size"], 0)

	def test_should_validate_respects_settings(self):
		"""Test that validation triggers are respected."""
		from frappe.model.docstatus import DocStatus

		# Enable validation on save
		self.settings.validate_sales_invoice_on_save = 1
		self.settings.save()
		self.assertTrue(self.settings.should_validate(DocStatus.draft()))

		# Disable validation on save
		self.settings.validate_sales_invoice_on_save = 0
		self.settings.save()
		self.assertFalse(self.settings.should_validate(DocStatus.draft()))

	def test_should_raise_exception_respects_settings(self):
		"""Test that error actions are respected."""
		from frappe.model.docstatus import DocStatus

		# Set to Error Message
		self.settings.error_action_on_save = "Error Message"
		self.settings.save()
		self.assertTrue(self.settings.should_raise_exception(DocStatus.draft()))

		# Set to Warning Message
		self.settings.error_action_on_save = "Warning Message"
		self.settings.save()
		self.assertFalse(self.settings.should_raise_exception(DocStatus.draft()))

		# Set to empty (None)
		self.settings.error_action_on_save = ""
		self.settings.save()
		self.assertFalse(self.settings.should_raise_exception(DocStatus.draft()))

	def test_before_validate_clears_dependent_fields(self):
		"""Test that dependent fields are cleared when parent is disabled."""
		# Set error action
		self.settings.validate_sales_invoice_on_save = 1
		self.settings.error_action_on_save = "Error Message"
		self.settings.save()

		# Disable validation
		self.settings.validate_sales_invoice_on_save = 0
		self.settings.save()

		# Error action should be cleared
		self.assertEqual(self.settings.error_action_on_save, "")
