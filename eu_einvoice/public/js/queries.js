frappe.provide("eu_einvoice.queries");

eu_einvoice.queries = {
	electronic_address_scheme: function (doc) {
		return {
			filters: {
				canonical_uri: ["in", ["urn:xoev-de:kosit:codeliste:eas", "urn:cef.eu:names:identifier:EAS"]],
			},
		};
	},
};
