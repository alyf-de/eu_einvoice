import frappe


def execute():
	"""
	In ERPNext v15 we've added Custom Fields for supplier number at customer
	and customer number at supplier. In ERPNext v16 they're part of ERPNext, so
	we remove them here.
	"""
	frappe.delete_doc_if_exists("Custom Field", "Customer-supplier_numbers")
	frappe.delete_doc_if_exists("Custom Field", "Supplier-customer_numbers")
