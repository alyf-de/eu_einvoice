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
				"depends_on": "eval:doc.e_invoice_import",
			},
			{
				"fieldname": "supplier_invoice_file",
				"label": _("Supplier Invoice File"),
				"insert_after": "bill_date",
				"fieldtype": "Attach",
			},
		],
		"Customer": [
			{
				"fieldname": "einvoice_tab",
				"label": _("E Invoicing"),
				"insert_after": "portal_users",
				"fieldtype": "Tab Break",
			},
			{
				"fieldname": "buyer_reference",
				"label": _("Buyer Reference"),
				"insert_after": "einvoice_tab",
				"fieldtype": "Data",
			},
			{
				"fieldname": "einvoice_profile",
				"label": _("E Invoice Profile"),
				"insert_after": "buyer_reference",
				"fieldtype": "Select",
				"options": PROFILE_OPTIONS,
				"default": "EXTENDED",
			},
			{
				"fieldname": "electronic_address_scheme",
				"label": _("Electronic Address Scheme"),
				"insert_after": "einvoice_profile",
				"fieldtype": "Link",
				"options": "Common Code",
			},
			{
				"fieldname": "electronic_address",
				"label": _("Electronic Address"),
				"insert_after": "electronic_address_scheme",
				"fieldtype": "Data",
				"depends_on": "electronic_address_scheme",
			},
		],
		"Company": [
			{
				"fieldname": "einvoice_tab",
				"label": _("E Invoicing"),
				"insert_after": "default_operating_cost_account",
				"fieldtype": "Tab Break",
			},
			{
				"fieldname": "electronic_address_scheme",
				"label": _("Electronic Address Scheme"),
				"insert_after": "einvoice_tab",
				"fieldtype": "Link",
				"options": "Common Code",
			},
			{
				"fieldname": "electronic_address",
				"label": _("Electronic Address"),
				"insert_after": "electronic_address_scheme",
				"fieldtype": "Data",
				"depends_on": "electronic_address_scheme",
			},
		],
		"Supplier": [
			{
				"fieldname": "einvoice_tab",
				"label": _("E Invoicing"),
				"insert_after": "portal_users",
				"fieldtype": "Tab Break",
			},
			{
				"fieldname": "electronic_address_scheme",
				"label": _("Electronic Address Scheme"),
				"insert_after": "einvoice_tab",
				"fieldtype": "Link",
				"options": "Common Code",
			},
			{
				"fieldname": "electronic_address",
				"label": _("Electronic Address"),
				"insert_after": "electronic_address_scheme",
				"fieldtype": "Data",
				"depends_on": "electronic_address_scheme",
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
				"fieldname": "einvoice_tab",
				"label": _("E Invoicing"),
				"insert_after": "terms",
				"fieldtype": "Tab Break",
			},
			{
				"fieldname": "e_invoice_validation_section",
				"label": "",
				"insert_after": "einvoice_tab",
				"fieldtype": "Section Break",
				"collapsible": 0,
			},
			{
				"fieldname": "einvoice_profile",
				"label": _("E Invoice Profile"),
				"insert_after": "e_invoice_validation_section",
				"fieldtype": "Select",
				"options": PROFILE_OPTIONS,
				"fetch_from": "customer.einvoice_profile",
				"fetch_if_empty": 1,
				"print_hide": 1,
			},
			{
				"fieldname": "einvoice_embedded_document",
				"label": _("Embedded Document"),
				"insert_after": "einvoice_profile",
				"fieldtype": "Attach",
				"depends_on": "einvoice_profile",
				"description": _("Additional supporting document to be embedded in the e-invoice file."),
			},
			{
				"fieldname": "einvoice_is_correct",
				"label": _("E Invoice Is Correct"),
				"insert_after": "e_invoice_validation_section",
				"fieldtype": "Check",
				"read_only": 1,
				"print_hide": 1,
				"no_copy": 1,
				"depends_on": "eval:!!doc.einvoice_profile",
			},
			{
				"fieldname": "validation_errors",
				"label": _("Validation Errors"),
				"insert_after": "einvoice_is_correct",
				"fieldtype": "Text",
				"read_only": 1,
				"print_hide": 1,
				"no_copy": 1,
				"depends_on": "eval:doc.einvoice_profile && !doc.einvoice_is_correct",
			},
			{
				"fieldname": "validation_warnings",
				"label": _("Validation Warnings"),
				"insert_after": "validation_errors",
				"fieldtype": "Text",
				"read_only": 1,
				"print_hide": 1,
				"no_copy": 1,
				"depends_on": "eval:doc.einvoice_profile && doc.validation_warnings",
			},
		],
	}
