// Copyright (c) 2025, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("E Invoice Settings", {
	onload(frm) {
		add_fields_to_mapping_table(frm);
	},
	refresh(frm) {
		add_fields_to_mapping_table(frm);
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

let add_fields_to_mapping_table = function (frm) {
	frappe.model.with_doctype("Sales Invoice", function () {
		let options = [];
		let meta = frappe.get_meta("Sales Invoice");
		options.push({
			label: "Name (name)",
			value: "name",
		});

		meta.fields.forEach((value) => {
			if (!["Section Break", "Column Break"].includes(value.fieldtype)) {
				options.push({
					label: value.label + " (" + value.fieldname + ")",
					value: value.fieldname,
				});
			}
		});

		const target_fields_actions = ["sales_invoice_number_field"];

		target_fields_actions.forEach((fieldname) => {
			frm.set_df_property(fieldname, "options", options);
		});
	});
};
