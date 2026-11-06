// Copyright (c) 2026, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("E Invoice Protocol", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.file) {
			frm.add_custom_button(__("View e-Invoice"), () => {
				eu_einvoice.viewer.open({ file_name: frm.doc.file });
			});
		}

		if (frm.doc.result === "Pending" && frm.doc.file) {
			frm.add_custom_button(__("Validate"), () => {
				frm.call({
					method: "enqueue_validation",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Queuing validation..."),
					callback(r) {
						if (!r.message) {
							return;
						}
						frappe.show_alert({
							message: __("Validation queued. Refresh this form in a moment."),
							indicator: "blue",
						});
					},
				});
			}).addClass("btn-primary");
		}
	},
});
