# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class EInvoiceProtocol(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link | None
		content_hash: DF.Data | None
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
		result: DF.Literal["Valid", "Invalid", "Unsupported"]
		ruleset_hash: DF.Data | None
		validation_errors: DF.Text | None
		validation_timestamp: DF.Datetime | None
		validation_warnings: DF.Text | None
		validator: DF.Data | None
		validator_version: DF.Data | None
		warning_count: DF.Int
	# end: auto-generated types

	pass
