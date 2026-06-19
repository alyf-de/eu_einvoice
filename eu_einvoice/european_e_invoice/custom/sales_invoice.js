frappe.ui.form.on("Sales Invoice", {
	setup(frm) {
		if (frm.fields_dict.einvoice_attachments) {
			frm.set_query("file", "einvoice_attachments", function () {
				if (frm.is_new()) {
					// No saved invoice name yet — files can only attach after save, so match nothing.
					return { filters: { name: ["in", []] } };
				}
				return {
					filters: {
						attached_to_doctype: frm.doctype,
						attached_to_name: frm.doc.name,
					},
				};
			});
		}
	},
	refresh: function (frm) {
		frm.trigger("add_einvoice_button");
		frm.trigger("setup_einvoice_attachment_grid_attach_button");

		if (!frm.is_dirty() && !frm.doc.einvoice_is_correct && frm.doc.einvoice_profile) {
			frm.dashboard.set_headline_alert(__("Please note the validation errors of the e-invoice."));
		}
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
	setup_einvoice_attachment_grid_attach_button(frm) {
		const table_field = frm.fields_dict.einvoice_attachments;
		if (
			!table_field?.grid ||
			!frm.doc.einvoice_profile ||
			frm.doc.docstatus !== 0 ||
			table_field.df.hidden
		) {
			return;
		}

		table_field.grid.add_custom_button(__("Attach file"), () => {
			open_einvoice_attachment_file_uploader(frm);
		});
	},
});

function open_einvoice_attachment_file_uploader(frm) {
	if (frm.is_new()) {
		frappe.msgprint({
			title: __("Save required"),
			message: __("Please save the Sales Invoice before attaching files."),
			indicator: "orange",
		});
		return;
	}

	new frappe.ui.FileUploader({
		doctype: frm.doctype,
		docname: frm.docname,
		fieldname: "einvoice_attachments",
		allow_multiple: false,
		make_attachments_public: frm.meta.make_attachments_public ? 1 : 0,
		on_success: (attachment) => {
			add_einvoice_attachment_row_from_upload(frm, attachment.file_doc || attachment);
		},
	});
}

function add_einvoice_attachment_row_from_upload(frm, file_doc) {
	const row = frm.add_child("einvoice_attachments");
	row.file = file_doc.name;
	row.file_name = file_doc.file_name;
	frm.refresh_field("einvoice_attachments");
}
