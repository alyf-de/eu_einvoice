// Copyright (c) 2024, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("E Invoice Import", {
	setup(frm) {
		frm.set_query("company", function () {
			return {
				filters: {
					is_group: 0,
				},
			};
		});

		frm.set_query("purchase_order", function (doc) {
			return {
				filters: {
					docstatus: 1,
					company: doc.company,
				},
			};
		});

		frm.set_query("supplier_address", function (doc) {
			return {
				filters: [
					["Dynamic Link", "link_doctype", "=", "Supplier"],
					["Dynamic Link", "link_name", "=", doc.supplier],
				],
			};
		});

		frm.set_query("item", "items", function (doc, cdt, cdn) {
			return {
				filters: {
					is_purchase_item: 1,
				},
			};
		});

		frm.set_query("tax_account", "taxes", function (doc, cdt, cdn) {
			return {
				filters: {
					account_type: "Tax",
					company: doc.company,
				},
			};
		});
	},
	refresh: function (frm) {
		const attach_field = frm.fields_dict["einvoice"];
		attach_field.on_attach_click = function () {
			attach_field.set_upload_options();
			attach_field.upload_options.restrictions.allowed_file_types = [
				"application/pdf",
				"application/xml",
				"text/xml",
			];
			attach_field.file_uploader = new frappe.ui.FileUploader(attach_field.upload_options);
		};

		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(
				__("Purchase Invoice"),
				function () {
					frappe.model.open_mapped_doc({
						method: "eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.create_purchase_invoice",
						frm: frm,
					});
				},
				__("Create")
			);
		}
	},
	create_supplier: function (frm) {
		frappe.model.open_mapped_doc({
			method: "eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.create_supplier",
			frm: frm,
		});
	},
	create_supplier_address: function (frm) {
		frappe.model.open_mapped_doc({
			method: "eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.create_supplier_address",
			frm: frm,
		});
	},
});

frappe.ui.form.on("E Invoice Item", {
	create_item: function (frm, cdt, cdn) {
		frappe.model.open_mapped_doc({
			method: "eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.create_item",
			source_name: cdn,
		});
	},
});
