// Copyright (c) 2025, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("E Invoice Settings", {
	refresh(frm) {
		frm.trigger("set_auto_attach_options");
	},

	set_auto_attach_options(frm) {
		frappe.model.with_doctype("Sales Invoice", function () {
			const fields = frappe.get_meta("Sales Invoice").fields;
			const attach_options = fields
				.filter((d) => d.fieldtype === "Attach")
				.map((d) => {
					return {
						value: d.fieldname,
						label: __(d.label),
					};
				});

			frm.fields_dict.attach_field_for_xml_file.set_data(
				attach_options.sort((a, b) => a.label.localeCompare(b.label))
			);
		});
	},
});
