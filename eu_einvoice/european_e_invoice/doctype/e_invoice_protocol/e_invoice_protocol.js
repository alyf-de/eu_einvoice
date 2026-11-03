// Copyright (c) 2026, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("E Invoice Protocol", {
	refresh(frm) {
		if (frm.doc.file && !frm.is_new()) {
			frm.add_custom_button(__("View e-Invoice"), () => {
				eu_einvoice.viewer.open({ file_name: frm.doc.file });
			});
		}
	},
});
