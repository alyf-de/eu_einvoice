# Copyright (c) 2025, ALYF GmbH and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class EInvoiceSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		validation_on_save_insert: DF.Literal["Silent Validation", "No Validation", "Warning", "Error"]
		validation_on_submit: DF.Literal["Silent Validation", "No Validation", "Warning", "Error"]
	# end: auto-generated types

	pass
