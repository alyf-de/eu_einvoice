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
