frappe.ui.form.on("Sales Invoice", {
	refresh: function (frm) {
		frm.trigger("add_einvoice_button");
	},
	add_einvoice_button: function (frm) {
		frm.page.add_menu_item(__("Download eInvoice"), () => {
			window.open(
				`/api/method/eu_einvoice.european_e_invoice.custom.sales_invoice.download_xrechnung?invoice_id=${encodeURIComponent(
					frm.doc.name
				)}`,
				"_blank"
			);
		});
	},
});
