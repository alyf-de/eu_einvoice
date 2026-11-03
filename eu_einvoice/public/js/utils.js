frappe.provide("eu_einvoice");

eu_einvoice.utils = {
	/**
	 * Supposed to be called when the user clicks the "Create Purchase Invoice"
	 * button in the error message, when the uploaded file in the E Invoice Import
	 * does not contain XML data.
	 *
	 * Needs to be a global function because it is called 'from the backend'.
	 */
	new_purchase_invoice: function () {
		frappe.new_doc("Purchase Invoice", {
			company: cur_frm.doc.company,
			supplier_invoice_file: cur_frm.doc.einvoice,
		});
	},
};

eu_einvoice.viewer = {
	/**
	 * Open a Dialog with the KoSIT HTML visualization of an e-invoice.
	 *
	 * @param {Object} opts
	 * @param {string} [opts.doctype]
	 * @param {string} [opts.name]
	 * @param {string} [opts.file_name] File document name
	 */
	open: function (opts) {
		const method = opts.file_name
			? "eu_einvoice.european_e_invoice.viewer.render_from_file"
			: "eu_einvoice.european_e_invoice.viewer.render_from_document";
		const args = opts.file_name
			? { file_name: opts.file_name }
			: { doctype: opts.doctype, name: opts.name };

		frappe.call({
			method: method,
			args: args,
			freeze: true,
			freeze_message: __("Rendering e-invoice…"),
			callback: (r) => {
				if (!r.message) {
					return;
				}
				const payload =
					typeof r.message === "string" ? { html: r.message, protocol: null } : r.message;
				eu_einvoice.viewer._show_dialog(payload.html, payload.protocol);
			},
		});
	},

	_show_dialog: function (html, protocol) {
		const dialog = new frappe.ui.Dialog({
			title: __("E-Invoice Viewer"),
			size: "extra-large",
		});
		if (protocol) {
			dialog.set_primary_action(__("Open Protocol"), () => {
				dialog.hide();
				frappe.set_route("Form", "E Invoice Protocol", protocol);
			});
		}
		const iframe = document.createElement("iframe");
		// No allow-same-origin: srcdoc would share Desk origin and enable parent XSS.
		// allow-scripts: KoSIT tab UI. allow-downloads: embedded attachment save.
		iframe.setAttribute("sandbox", "allow-scripts allow-downloads");
		iframe.setAttribute("referrerpolicy", "no-referrer");
		iframe.style.width = "100%";
		iframe.style.height = "70vh";
		iframe.style.border = "0";
		dialog.$body.empty().append(iframe);
		dialog.show();
		iframe.srcdoc = html;
	},

	/**
	 * Add a file status icon next to attachment / Attach-field labels when a protocol exists.
	 */
	decorate_form: function (frm) {
		if (!frm || frm.is_new() || !frm.$wrapper) {
			return;
		}
		eu_einvoice.viewer._decorate_sidebar_attachments(frm);
		eu_einvoice.viewer._decorate_attach_fields(frm);
	},

	_eye_button: function (file_name, result) {
		// span (not <a>/<button>): sidebar pills are already <button> elements
		const status_class =
			result === "Valid"
				? "is-valid"
				: result === "Invalid"
					? "is-invalid"
					: result === "Unsupported"
						? "is-unsupported"
						: "is-pending";
		const icon_name =
			result === "Valid" ? "file-check" : result === "Invalid" ? "file-x" : "file";
		const $btn = $(`<span class="eu-einvoice-view-btn ${status_class}" role="button" tabindex="0" title="${__(
			"View e-Invoice"
		)}" aria-label="${__("View e-Invoice")}">
			${frappe.utils.icon(icon_name, "sm")}
		</span>`);
		const open = (event) => {
			event.preventDefault();
			event.stopPropagation();
			eu_einvoice.viewer.open({ file_name });
		};
		$btn.on("click", open);
		$btn.on("keydown", (event) => {
			if (event.key === "Enter" || event.key === " ") {
				open(event);
			}
		});
		return $btn;
	},

	_decorate_sidebar_attachments: async function (frm) {
		const attachments = frm.get_docinfo()?.attachments || [];
		if (!attachments.length || !frm.attachments?.parent) {
			return;
		}

		frm.attachments.parent.find(".eu-einvoice-view-btn").remove();

		const file_names = attachments.map((row) => row.name).filter(Boolean);
		const by_file = await frappe.xcall(
			"eu_einvoice.european_e_invoice.viewer.files_with_protocol",
			{ identifiers: file_names }
		);
		if (!by_file || !Object.keys(by_file).length) {
			return;
		}

		attachments.forEach((attachment) => {
			const result = by_file[attachment.name];
			if (!result) {
				return;
			}
			const $row = frm.attachments.parent.find(
				`.attachment-row a.attachment-icon[href$="/file/${attachment.name}"]`
			).closest(".attachment-row");
			if (!$row.length || $row.find(".eu-einvoice-view-btn").length) {
				return;
			}
			// Sibling of the truncated label flex, immediately left of the remove (X) control
			const $pill = $row.find(".data-pill");
			const $btn = eu_einvoice.viewer._eye_button(attachment.name, result);
			const $remove = $pill.children(".remove-btn");
			if ($remove.length) {
				$remove.before($btn);
			} else {
				$pill.append($btn);
			}
		});
	},

	_decorate_attach_fields: async function (frm) {
		const attach_fields = (frm.meta?.fields || []).filter((df) =>
			["Attach", "Attach Image"].includes(df.fieldtype)
		);
		if (!attach_fields.length) {
			return;
		}

		const urls = [];
		attach_fields.forEach((df) => {
			const value = frm.doc[df.fieldname];
			if (value) {
				urls.push(value);
			}
			frm.$wrapper.find(`[data-fieldname="${df.fieldname}"] .eu-einvoice-view-btn`).remove();
		});
		if (!urls.length) {
			return;
		}

		const by_file = await frappe.xcall(
			"eu_einvoice.european_e_invoice.viewer.files_with_protocol",
			{ identifiers: urls }
		);
		const file_names = Object.keys(by_file || {});
		if (!file_names.length) {
			return;
		}

		const files = await frappe.db.get_list("File", {
			filters: { name: ["in", file_names] },
			fields: ["name", "file_url"],
			limit: file_names.length,
		});
		const url_to_name = {};
		files.forEach((row) => {
			if (row.file_url) {
				url_to_name[row.file_url] = row.name;
			}
		});

		attach_fields.forEach((df) => {
			const value = frm.doc[df.fieldname];
			const file_name = url_to_name[value];
			if (!file_name) {
				return;
			}
			const $link = frm.get_field(df.fieldname)?.$value?.find(".attached-file-link");
			if (!$link?.length || $link.siblings(".eu-einvoice-view-btn").length) {
				return;
			}
			$link.after(eu_einvoice.viewer._eye_button(file_name, by_file[file_name]));
		});
	},
};

$(document).on("form-refresh", (_event, frm) => {
	if (!frm?.$wrapper) {
		return;
	}
	frm.$wrapper
		.off("attachments_change.eu_einvoice")
		.on("attachments_change.eu_einvoice", () => {
			eu_einvoice.viewer.decorate_form(frm);
		});
	eu_einvoice.viewer.decorate_form(frm);
});
