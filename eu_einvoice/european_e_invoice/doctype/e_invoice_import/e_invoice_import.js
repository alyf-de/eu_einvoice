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

		frm.set_query("po_detail", "items", function (doc, cdt, cdn) {
			const row = locals[cdt][cdn];
			return {
				query: "eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.po_item_query",
				filters: {
					parent: doc.purchase_order,
					item_code: row.item,
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
			const { linked_invoice, unlinked_invoice } = frm.doc.__onload;
			if (linked_invoice && frappe.model.can_read("Purchase Invoice")) {
				frm.add_custom_button(__("View {0}", [linked_invoice]), function () {
					frappe.set_route("Form", "Purchase Invoice", linked_invoice);
				});
			} else if (unlinked_invoice && frappe.model.can_write("Purchase Invoice")) {
				frm.add_custom_button(__("Link to {0}", [unlinked_invoice]), function () {
					frappe
						.xcall(
							"eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.link_to_purchase_invoice",
							{
								einvoice: frm.doc.name,
								purchase_invoice: unlinked_invoice,
							}
						)
						.then(() => {
							frm.reload_doc();
						});
				});
			} else if (frappe.model.can_create("Purchase Invoice")) {
				frm.add_custom_button(__("Create Purchase Invoice"), function () {
					frappe.model.open_mapped_doc({
						method: "eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.create_purchase_invoice",
						frm: frm,
					});
				});
			}
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

	po_detail: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.po_detail || (row.item && row.uom) || !frappe.model.can_read("Purchase Order")) {
			return;
		}

		frappe
			.xcall(
				"eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.get_po_item_details",
				{ po_detail: row.po_detail }
			)
			.then((r) => {
				if (r.item_code && !row.item) {
					frappe.model.set_value(cdt, cdn, "item", r.item_code);
				}
				if (r.uom && !row.uom) {
					frappe.model.set_value(cdt, cdn, "uom", r.uom);
				}
			});
	},
});
