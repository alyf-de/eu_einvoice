# Copyright (c) 2024, ALYF GmbH and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class EInvoicePaymentTerm(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		discount_actual_amount: DF.Currency
		discount_basis_date: DF.Date | None
		discount_calculation_percent: DF.Percent
		due: DF.Date
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		partial_amount: DF.Currency
	# end: auto-generated types

	pass
