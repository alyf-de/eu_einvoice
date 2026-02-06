// Copyright (c) 2025, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("E Invoice Settings", {
	onload(frm) {
		frm.trigger("set_invoice_number_field_options");
	},

	refresh(frm) {
		frm.trigger("set_invoice_number_field_options");
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

	set_invoice_number_field_options(frm) {
		frappe.model.with_doctype("Sales Invoice", function () {
			const meta = frappe.get_meta("Sales Invoice");
			const options = meta.fields
				.filter((d) => ["Data", "Read Only"].includes(d.fieldtype))
				.map((value) => {
					return {
						label: `${__(value.label, null, "Sales Invoice")} (${value.fieldname})`,
						value: value.fieldname,
					};
				})
				.sort((a, b) => a.label.localeCompare(b.label));

			frm.set_df_property("sales_invoice_number_field", "options", [
				{
					// Empty option is the default case, when this feature is not used
					label: "",
					value: "",
				},
				...options,
			]);
		});
	},
});
