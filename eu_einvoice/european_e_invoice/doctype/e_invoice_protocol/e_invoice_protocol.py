# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class EInvoiceProtocol(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		content_hash: DF.Data | None
		copied_from: DF.Link | None
		detection_timestamp: DF.Datetime | None
		einvoice_format: DF.Data | None
		error_count: DF.Int
		file: DF.Link | None
		file_hash: DF.Data | None
		file_name: DF.Data | None
		invoice_profile: DF.Data | None
		message_count: DF.Int
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		result: DF.Literal["Pending", "Valid", "Invalid", "Unsupported"]
		validation_errors: DF.Text | None
		validation_timestamp: DF.Datetime | None
		validation_warnings: DF.Text | None
		validator: DF.Data | None
		validator_version: DF.Data | None
		warning_count: DF.Int
	# end: auto-generated types

	def after_insert(self):
		# Stub created already settled (copied on upload) - still fill older Pending rows
		self._maybe_propagate_validation(force=True)

	def on_update(self):
		self._maybe_propagate_validation()

	def _maybe_propagate_validation(self, force: bool = False):
		"""When this protocol is settled, copy results onto Pending siblings (same hashes)."""
		if self.flags.get("skip_validation_propagate"):
			return
		if self.result not in ("Valid", "Invalid", "Unsupported"):
			return
		if not force and not (
			self.has_value_changed("result") or self.has_value_changed("validation_timestamp")
		):
			return

		from eu_einvoice.european_e_invoice.protocol import propagate_validation_to_pending_siblings

		propagate_validation_to_pending_siblings(self)
