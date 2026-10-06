import frappe
from frappe.tests.utils import FrappeTestCase

from eu_einvoice.european_e_invoice.doctype.e_invoice_attachment_row.e_invoice_attachment_row import (
	on_doctype_update,
)


class TestEInvoiceAttachmentRow(FrappeTestCase):
	def test_unique_parent_file_name_constraint(self):
		on_doctype_update()

		if frappe.db.db_type == "mariadb":
			rows = frappe.db.sql(
				"""
				SELECT CONSTRAINT_NAME
				FROM information_schema.TABLE_CONSTRAINTS
				WHERE table_name = 'tabE Invoice Attachment Row'
					AND constraint_type = 'UNIQUE'
					AND CONSTRAINT_NAME = 'unique_parent_file_name'
				"""
			)
		elif frappe.db.db_type == "postgres":
			rows = frappe.db.sql(
				"""
				SELECT con.conname AS constraint_name
				FROM pg_constraint con
				INNER JOIN pg_class rel ON rel.oid = con.conrelid
				WHERE rel.relname = 'tabE Invoice Attachment Row'
					AND con.contype = 'u'
					AND con.conname = 'unique_parent_file_name'
				"""
			)
		else:
			self.skipTest(f"Unique constraint check not implemented for {frappe.db.db_type}")

		self.assertTrue(rows)
