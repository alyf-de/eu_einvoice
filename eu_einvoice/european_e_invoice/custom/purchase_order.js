frappe.ui.form.on("Purchase Order", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(
				__("E Invoice Import"),
				function () {
					frappe.model.open_mapped_doc({
						method: "eu_einvoice.european_e_invoice.doctype.e_invoice_import.e_invoice_import.create_einvoice_from_po",
						frm: frm,
					});
				},
				__("Create")
			);
		}
	},
});
