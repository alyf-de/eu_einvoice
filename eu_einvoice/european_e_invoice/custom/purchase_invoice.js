frappe.ui.form.on("Purchase Invoice", {
	refresh: function (frm) {
		frm.trigger("add_einvoice_button");

		if (!frm.is_dirty() && !frm.doc.einvoice_is_correct && frm.doc.einvoice_profile) {
			frm.dashboard.set_headline_alert(__("Please note the validation errors of the e-invoice."));
		}
	},
	supplier: function (frm) {
		// The supplier's self-billing profile is a default. Other scripts may change it afterwards.
		// ERPNext also triggers this event when a new document with a supplier is loaded.
		if (frm.doc.e_invoice_import) {
			// The supplier sent their own e-invoice, so we don't bill ourselves
			return;
		}

		if (frm.doc.__run_link_triggers === false) {
			// Like ERPNext's payment terms: duplicates and amendments keep the copied profile
			return;
		}

		if (!frm.doc.supplier) {
			return frm.set_value("einvoice_profile", "");
		}

		return frappe.db
			.get_value("Supplier", frm.doc.supplier, "self_billing_einvoice_profile")
			.then((r) => frm.set_value("einvoice_profile", r.message.self_billing_einvoice_profile || ""));
	},
	add_einvoice_button: function (frm) {
		if (frm.is_new() || !frm.doc.einvoice_profile) {
			return;
		}

		frm.page.add_menu_item(__("Download eInvoice"), () => {
			const args = new URLSearchParams({ doctype: frm.doc.doctype, name: frm.doc.name });
			window.open(
				`/api/method/eu_einvoice.european_e_invoice.einvoice.download_einvoice?${args}`,
				"_blank"
			);
		});
	},
});
