frappe.ui.form.on("Sales Invoice", {
	onload: function (frm) {
		frm.page.add_menu_item(__("Download XRechnung"), () => {
			window.open(
				`/api/method/eu_einvoice.european_e_invoice.custom.sales_invoice.download_xrechnung?invoice_id=${encodeURIComponent(
					frm.doc.name
				)}`,
				"_blank"
			);
		});
	},
});
