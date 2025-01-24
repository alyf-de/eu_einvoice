from .utils import identity as _


def get_custom_fields():
	PROFILE_OPTIONS = "\n".join(
		[
			"",
			"BASIC",
			"EN 16931",
			"EXTENDED",
			"XRECHNUNG",
		]
	)

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
		"Customer": [
			{
				"fieldname": "buyer_reference",
				"label": _("Buyer Reference"),
				"insert_after": "language",
				"fieldtype": "Data",
			},
		],
		"Sales Order": [
			{
				"fieldname": "buyer_reference",
				"label": _("Buyer Reference"),
				"insert_after": "tax_id",
				"fieldtype": "Data",
				"fetch_from": "customer.buyer_reference",
				"fetch_if_empty": 1,
			},
		],
		"Sales Invoice": [
			{
				"fieldname": "buyer_reference",
				"label": _("Buyer Reference"),
				"insert_after": "tax_id",
				"fieldtype": "Data",
				"fetch_from": "customer.buyer_reference",
				"fetch_if_empty": 1,
			},
			{
				"fieldname": "e_invoice_validation_section",
				"label": _("E Invoice Validation"),
				"insert_after": "remarks",
				"fieldtype": "Section Break",
				"collapsible": 1,
			},
			{
				"fieldname": "einvoice_profile",
				"label": _("E Invoice Profile"),
				"insert_after": "e_invoice_validation_section",
				"fieldtype": "Select",
				"options": PROFILE_OPTIONS,
				"print_hide": 1,
			},
			{
				"fieldname": "einvoice_is_correct",
				"label": _("E Invoice Is Correct"),
				"insert_after": "e_invoice_validation_section",
				"fieldtype": "Check",
				"read_only": 1,
				"print_hide": 1,
				"depends_on": "eval:!!doc.einvoice_profile",
			},
			{
				"fieldname": "validation_errors",
				"label": _("Validation Errors"),
				"insert_after": "einvoice_is_correct",
				"fieldtype": "Text",
				"read_only": 1,
				"print_hide": 1,
				"depends_on": "eval:doc.einvoice_profile && !doc.einvoice_is_correct",
			},
		],
	}
