import frappe


def execute():
	"""Set the default settings for the new 'E Invoice Settings' doctype."""
	settings = frappe.get_single("E Invoice Settings")
	settings.validate_sales_invoice_on_save = 1
	settings.validate_sales_invoice_on_submit = 1
	settings.error_action_on_save = ""
	settings.error_action_on_submit = ""
	settings.save()
