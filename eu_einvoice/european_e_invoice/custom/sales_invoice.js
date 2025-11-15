frappe.ui.form.on("Sales Invoice", {
	onload: function (frm) {
		// Set default e-invoice profile from settings for new documents
		if (frm.is_new() && !frm.doc.einvoice_profile) {
			frm.trigger("set_default_einvoice_profile");
		}
	},

	refresh: function (frm) {
		frm.trigger("add_einvoice_button");

		if (!frm.is_dirty() && !frm.doc.einvoice_is_correct && frm.doc.einvoice_profile) {
			frm.dashboard.set_headline_alert(
				__("Please note the validation errors of the e-invoice.")
			);
		}
	},

	customer: function (frm) {
		// When customer changes, set profile from customer or settings
		if (frm.doc.customer) {
			frm.trigger("set_default_einvoice_profile");
		}
	},

	set_default_einvoice_profile: function (frm) {
		// Only set default if profile is empty
		if (frm.doc.einvoice_profile) {
			return;
		}

		// Get settings
		frappe.db.get_single_value("E Invoice Settings", "auto_set_profile_from_customer").then((auto_set) => {
			if (auto_set && frm.doc.customer) {
				// Try to get profile from customer first
				frappe.db.get_value("Customer", frm.doc.customer, "einvoice_profile").then((r) => {
					if (r && r.einvoice_profile) {
						frm.set_value("einvoice_profile", r.einvoice_profile);
					} else {
						// Fall back to settings default
						frm.trigger("set_default_from_settings");
					}
				});
			} else {
				// Use settings default
				frm.trigger("set_default_from_settings");
			}
		});
	},

	set_default_from_settings: function (frm) {
		frappe.db.get_single_value("E Invoice Settings", "default_einvoice_profile").then((default_profile) => {
			if (default_profile && !frm.doc.einvoice_profile) {
				frm.set_value("einvoice_profile", default_profile);
			}
		});
	},

	add_einvoice_button: function (frm) {
		if (frm.is_new() || !frm.doc.einvoice_profile) {
			return;
		}

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
