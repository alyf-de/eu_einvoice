from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from .custom_fields import get_custom_fields


def after_install():
	create_custom_fields(get_custom_fields())
