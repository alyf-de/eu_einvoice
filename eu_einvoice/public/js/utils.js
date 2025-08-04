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
