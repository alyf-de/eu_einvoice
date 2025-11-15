# Copyright (c) 2025, ALYF GmbH and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.docstatus import DocStatus
from frappe.model.document import Document


class EInvoiceSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		alert_email: DF.Data | None
		alert_on_validation_failure: DF.Check
		auto_create_items: DF.Check
		auto_create_supplier: DF.Check
		auto_match_po: DF.Check
		auto_set_profile_from_customer: DF.Check
		default_business_process: DF.Data | None
		default_einvoice_profile: DF.Literal["", "BASIC", "EN 16931", "EXTENDED", "XRECHNUNG"]
		embed_xml_in_pdf: DF.Check
		enable_audit_log: DF.Check
		enable_pdfa_conversion: DF.Check
		enable_schematron_caching: DF.Check
		error_action_on_save: DF.Literal["", "Warning Message", "Error Message"]
		error_action_on_submit: DF.Literal["", "Warning Message", "Error Message"]
		import_default_expense_account: DF.Link | None
		import_validation_strictness: DF.Literal[
			"Lenient (Allow Warnings)", "Strict (Block on Warnings)", "Very Strict (Block on Schema Errors)"
		]
		pdf_creator: DF.Data | None
		pdf_producer: DF.Data | None
		pdfa_fallback_behavior: DF.Literal["Log Error and Continue", "Show Warning to User", "Block PDF Download"]
		require_buyer_reference: DF.Check
		validate_sales_invoice_on_save: DF.Check
		validate_sales_invoice_on_submit: DF.Check
		validate_xrechnung_against_en16931: DF.Check
	# end: auto-generated types

	def validate(self):
		"""Validate settings."""
		self._validate_business_process_urn()
		self._validate_expense_account()
		self._validate_alert_email()
		self._check_ghostscript_availability()

	def before_validate(self):
		"""Clear dependent fields if parent field is disabled."""
		if not self.validate_sales_invoice_on_save:
			self.error_action_on_save = ""
		if not self.validate_sales_invoice_on_submit:
			self.error_action_on_submit = ""

	def _validate_business_process_urn(self):
		"""Validate Business Process URN format."""
		if self.default_business_process:
			# URN format: urn:scheme:specific-string
			urn_pattern = r"^urn:[a-z0-9][a-z0-9-]{0,31}:[a-z0-9()+,\-.:=@;$_!*'%/?#]+$"
			if not re.match(urn_pattern, self.default_business_process, re.IGNORECASE):
				frappe.msgprint(
					_("Invalid Business Process URN format. Expected format: urn:scheme:specific-string"),
					alert=True,
					indicator="orange",
				)

	def _validate_expense_account(self):
		"""Validate that expense account is an expense account."""
		if self.import_default_expense_account:
			account_type = frappe.db.get_value("Account", self.import_default_expense_account, "account_type")
			if account_type not in ("Expense Account", "Cost of Goods Sold"):
				frappe.throw(
					_("Import Default Expense Account must be of type 'Expense Account' or 'Cost of Goods Sold'")
				)

	def _validate_alert_email(self):
		"""Validate alert email format."""
		if self.alert_on_validation_failure and self.alert_email:
			# Simple email validation
			if not re.match(r"[^@]+@[^@]+\.[^@]+", self.alert_email):
				frappe.throw(_("Invalid alert email address format"))

	def _check_ghostscript_availability(self):
		"""Check if Ghostscript is installed if PDF/A conversion is enabled."""
		if self.enable_pdfa_conversion:
			import shutil

			if not shutil.which("gs"):
				frappe.msgprint(
					_(
						"Ghostscript is not installed. PDF/A-3 conversion will not work. "
						"Install Ghostscript with: sudo apt-get install ghostscript"
					),
					alert=True,
					indicator="orange",
					title=_("Ghostscript Not Found"),
				)

	def should_validate(self, docstatus: DocStatus) -> bool:
		"""Return True if a Sales Invoice should be validated."""
		return (docstatus == DocStatus.submitted() and self.validate_sales_invoice_on_submit) or (
			docstatus == DocStatus.draft() and self.validate_sales_invoice_on_save
		)

	def should_raise_exception(self, docstatus: DocStatus) -> bool:
		"""Return True if the error action is set to 'Error Message'."""
		return (docstatus == DocStatus.submitted() and self.error_action_on_submit == "Error Message") or (
			docstatus == DocStatus.draft() and self.error_action_on_save == "Error Message"
		)

	def should_show_message(self, docstatus: DocStatus) -> bool:
		"""Return True if any error action is set."""
		return (docstatus == DocStatus.submitted() and self.error_action_on_submit) or (
			docstatus == DocStatus.draft() and self.error_action_on_save
		)

	@frappe.whitelist()
	def get_statistics(self):
		"""Get usage statistics for e-invoices.

		Returns:
		    Dict with statistics about e-invoice generation and validation
		"""
		from frappe.utils import add_to_date, now_datetime

		last_30_days = add_to_date(now_datetime(), days=-30)

		# Get basic counts
		total_generated = frappe.db.count(
			"Sales Invoice",
			filters={"einvoice_profile": ["!=", ""], "creation": [">=", last_30_days]},
		)

		validation_passed = frappe.db.count(
			"Sales Invoice",
			filters={"einvoice_is_correct": 1, "creation": [">=", last_30_days]},
		)

		validation_failed = frappe.db.count(
			"Sales Invoice",
			filters={
				"einvoice_is_correct": 0,
				"einvoice_profile": ["!=", ""],
				"creation": [">=", last_30_days],
			},
		)

		total_imported = frappe.db.count("E Invoice Import", filters={"creation": [">=", last_30_days]})

		# Get profile breakdown
		profile_breakdown = frappe.db.sql(
			"""
			SELECT einvoice_profile, COUNT(*) as count
			FROM `tabSales Invoice`
			WHERE einvoice_profile IS NOT NULL
			AND einvoice_profile != ''
			AND creation >= %s
			GROUP BY einvoice_profile
			ORDER BY count DESC
		""",
			last_30_days,
			as_dict=True,
		)

		# Get cache info
		from eu_einvoice.schematron import get_cache_info

		cache_info = get_cache_info()

		return {
			"total_generated": total_generated,
			"validation_passed": validation_passed,
			"validation_failed": validation_failed,
			"total_imported": total_imported,
			"profile_breakdown": profile_breakdown,
			"cache_info": cache_info,
			"success_rate": round((validation_passed / total_generated * 100) if total_generated > 0 else 0, 1),
		}

	@frappe.whitelist()
	def clear_validation_cache(self):
		"""Clear the Schematron validation cache.

		Returns:
		    Dict with success message
		"""
		from eu_einvoice.schematron import clear_cache, get_cache_info

		# Get cache info before clearing
		cache_before = get_cache_info()

		# Clear the cache
		clear_cache()

		# Get cache info after clearing
		cache_after = get_cache_info()

		frappe.msgprint(
			_(
				"Schematron stylesheet cache cleared. "
				"Freed {0} compiled stylesheets from memory."
			).format(cache_before["cache_size"]),
			indicator="green",
		)

		return {
			"message": "Cache cleared successfully",
			"cache_before": cache_before,
			"cache_after": cache_after,
		}
