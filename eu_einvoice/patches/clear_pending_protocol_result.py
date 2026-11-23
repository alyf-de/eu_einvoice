import frappe


def execute():
	"""Pending was removed from **E Invoice Protocol** *Result*; clear leftover rows."""
	if not frappe.db.exists("DocType", "E Invoice Protocol"):
		return
	if not frappe.db.has_column("E Invoice Protocol", "result"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabE Invoice Protocol`
		SET result = 'Unsupported'
		WHERE result = 'Pending'
		"""
	)
