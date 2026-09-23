frappe.ui.form.on("Customer", {
	setup: function (frm) {
		frm.set_query("electronic_address_scheme", eu_einvoice.queries.electronic_address_scheme);
	},
});
