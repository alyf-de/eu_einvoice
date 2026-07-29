# Copyright (c) 2025, ALYF GmbH and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.docstatus import DocStatus
from frappe.model.document import Document
from frappe.utils.background_jobs import create_job_id, enqueue

from eu_einvoice.european_e_invoice.custom.sales_invoice_attachments import (
	BULK_MIGRATE_LEGACY_EMBED_JOB_ID,
	set_embed_attachment_field_exclusivity,
)


class EInvoiceSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		attach_field_for_xml_file: DF.Autocomplete | None
		auto_attach_xml: DF.Check
		auto_name_format_for_xml_file: DF.Data | None
		error_action_on_save: DF.Literal["", "Warning Message", "Error Message"]
		error_action_on_submit: DF.Literal["", "Warning Message", "Error Message"]
		multi_attachment_embed_enabled: DF.Check
		sales_invoice_number_field: DF.Autocomplete | None
		validate_sales_invoice_on_save: DF.Check
		validate_sales_invoice_on_submit: DF.Check
		vat_exemption_reason_text: DF.SmallText | None
	# end: auto-generated types

	@frappe.whitelist()
	def import_code_lists(self):
		"""Import the bundled EN 16931 code lists into an existing site."""
		frappe.only_for("System Manager")
		# ~4600 Common Codes, too slow for a request
		frappe.enqueue(
			"eu_einvoice.install.import_code_lists",
			queue="long",
			timeout=1800,
			enqueue_after_commit=True,
		)

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

	def on_update(self):
		"""Apply exclusive legacy / table field visibility when the setting changes."""
		set_embed_attachment_field_exclusivity(bool(self.multi_attachment_embed_enabled))

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


@frappe.whitelist(methods=["POST"])
def migrate_attachments_to_table(
	include_submitted: bool | int = 0,
	remove_broken_links: bool | int = 0,
) -> dict[str, str | bool]:
	"""Enqueue a background job to migrate legacy embed attachments site-wide.

	Requires **System Manager** and ``multi_attachment_embed_enabled`` on
	**E Invoice Settings**.

	Args:
		include_submitted (bool | int, optional): Pass ``1`` to include submitted and
			cancelled **Sales Invoice** documents.
		remove_broken_links (bool | int, optional): Pass ``1`` to clear unresolvable
			legacy URLs instead of skipping them.

	Returns:
		dict[str, str | bool]: ``job_id`` (RQ Job name) and ``queued`` (``True`` when a
			new job was enqueued, ``False`` when a deduplicated job is already running).

	Raises:
		frappe.PermissionError: When the caller is not **System Manager**.
		frappe.ValidationError: When multi-attachment embedding is disabled.
	"""
	frappe.only_for("System Manager")

	if not frappe.db.get_single_value("E Invoice Settings", "multi_attachment_embed_enabled"):
		frappe.throw(_("Enable Multiple Attachment Embedding first."))

	from frappe.utils import cint, get_link_to_form

	namespaced_job_id = create_job_id(BULK_MIGRATE_LEGACY_EMBED_JOB_ID)
	job = enqueue(
		"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.bulk_migrate_legacy_embed_attachments",
		queue="long",
		timeout=1500,
		job_id=BULK_MIGRATE_LEGACY_EMBED_JOB_ID,
		deduplicate=True,
		include_submitted=cint(include_submitted),
		remove_broken_links=cint(remove_broken_links),
	)

	if job:
		frappe.msgprint(
			_("Migration queued. Track progress in {0}.").format(get_link_to_form("RQ Job", job.id)),
			indicator="blue",
		)
		return {"job_id": job.id, "queued": True}

	frappe.msgprint(
		_("Migration already queued. Track progress in {0}.").format(
			get_link_to_form("RQ Job", namespaced_job_id)
		),
		indicator="orange",
	)
	return {"job_id": namespaced_job_id, "queued": False}
