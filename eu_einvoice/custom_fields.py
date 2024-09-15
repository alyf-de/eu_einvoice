from .utils import identity as _


def get_custom_fields():
	return {
		"Purchase Invoice": [
			{
				"fieldname": "e_invoice_import",
				"label": _("E Invoice Import"),
				"insert_after": "bill_no",
				"fieldtype": "Link",
				"options": "E Invoice Import",
				"read_only": 1,
			}
		],
	}
