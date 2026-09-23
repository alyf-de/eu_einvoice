frappe.ui.form.on("Supplier", {
	setup: function (frm) {
		frm.set_query("electronic_address_scheme", eu_einvoice.queries.electronic_address_scheme);
	},
});
