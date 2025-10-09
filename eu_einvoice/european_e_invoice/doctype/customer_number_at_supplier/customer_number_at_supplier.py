# Copyright (c) 2025, ALYF GmbH and contributors
# For license information, please see license.txt

# NOTE: starting from v16, this DocType will be part of ERPNext core.

# import frappe
from frappe.model.document import Document


class CustomerNumberAtSupplier(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link | None
		customer_number: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
	# end: auto-generated types

	pass
