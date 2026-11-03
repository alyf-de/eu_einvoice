// Copyright (c) 2026, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.listview_settings["E Invoice Protocol"] = {
	add_fields: ["result"],
	get_indicator(doc) {
		if (doc.result === "Valid") {
			return [__("Valid"), "green", "result,=,Valid"];
		}
		if (doc.result === "Invalid") {
			return [__("Invalid"), "red", "result,=,Invalid"];
		}
		if (doc.result === "Unsupported") {
			return [__("Unsupported"), "grey", "result,=,Unsupported"];
		}
		return [__("Pending"), "orange", "result,=,Pending"];
	},
};
