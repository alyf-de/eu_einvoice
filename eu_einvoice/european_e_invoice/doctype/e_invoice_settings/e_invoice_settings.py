# Copyright (c) 2025, ALYF GmbH and contributors
# For license information, please see license.txt

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

		attach_field_for_xml_file: DF.Autocomplete | None
		auto_attach_xml: DF.Check
		error_action_on_save: DF.Literal["", "Warning Message", "Error Message"]
		error_action_on_submit: DF.Literal["", "Warning Message", "Error Message"]
		validate_sales_invoice_on_save: DF.Check
		validate_sales_invoice_on_submit: DF.Check
	# end: auto-generated types

	def before_validate(self):
		if not self.validate_sales_invoice_on_save:
			self.error_action_on_save = ""
		if not self.validate_sales_invoice_on_submit:
			self.error_action_on_submit = ""

	def validate(self):
		"""Validate E Invoice Settings before save."""
		# Only validate field if both auto-attach is enabled AND a field is specified
		if self.auto_attach_xml and self.attach_field_for_xml_file:
			self._validate_attach_field()

	def _validate_attach_field(self):
		"""Validate that the selected attachment field exists and is of type Attach."""
		# Get Sales Invoice doctype
		sales_invoice_meta = frappe.get_meta("Sales Invoice")

		# Check if field exists
		field = sales_invoice_meta.get_field(self.attach_field_for_xml_file)

		if not field:
			frappe.throw(
				_("Field '{0}' does not exist on Sales Invoice doctype").format(
					self.attach_field_for_xml_file
				)
			)

		# Check if field is of type Attach
		if field.fieldtype != "Attach":
			frappe.throw(
				_("Field '{0}' must be of type 'Attach'. Current type: {1}").format(
					self.attach_field_for_xml_file, field.fieldtype
				)
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
