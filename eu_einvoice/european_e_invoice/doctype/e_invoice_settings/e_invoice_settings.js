// Copyright (c) 2025, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("E Invoice Settings", {
	refresh(frm) {
		frm.trigger("set_invoice_number_field_options");
		frm.trigger("set_auto_attach_options");
		frm.add_custom_button(__("Import Code Lists"), () => frm.trigger("import_code_lists"));
	},

	async import_code_lists(frm) {
		await frm.call("import_code_lists");
		frappe.show_alert({
			message: __("Import of code lists queued. This may take a few minutes."),
			indicator: "green",
		});
	},

	btn_migrate_attachments_to_table(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Migrate Existing Attachments to Table"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "description",
					options: __(
						"Move legacy <b>Embedded Document</b> links into the <b>Embedded Documents</b> table for all Sales Invoices."
					),
				},
				{
					fieldtype: "Check",
					fieldname: "remove_broken_links",
					label: __("Remove broken legacy file links"),
					description: __(
						"Clear unresolvable links from the legacy field instead of leaving them unchanged. Each removal is logged."
					),
					default: 0,
				},
			],
			primary_action_label: __("Start migration"),
			primary_action(values) {
				dialog.hide();
				frappe.call({
					method: "eu_einvoice.european_e_invoice.doctype.e_invoice_settings.e_invoice_settings.migrate_attachments_to_table",
					args: {
						remove_broken_links: values.remove_broken_links ? 1 : 0,
					},
					freeze: true,
					freeze_message: __("Queuing migration..."),
				});
			},
		});
		dialog.show();
	},

	async set_auto_attach_options(frm) {
		const options = await get_autocomplete_options("Sales Invoice", ["Attach"]);
		frm.fields_dict.attach_field_for_xml_file.set_data(options);
	},

	async set_invoice_number_field_options(frm) {
		const options = await get_autocomplete_options("Sales Invoice", ["Data", "Read Only"]);
		frm.fields_dict.sales_invoice_number_field.set_data(options);
	},
});

async function get_autocomplete_options(doctype, allowed_fieldtypes) {
	await frappe.model.with_doctype(doctype);
	const meta = frappe.get_meta(doctype);
	return meta.fields
		.filter((d) => allowed_fieldtypes.includes(d.fieldtype))
		.map((value) => {
			return {
				label: __(value.label, null, doctype),
				description: value.fieldname,
				value: value.fieldname,
			};
		})
		.sort((a, b) => a.label.localeCompare(b.label));
}
