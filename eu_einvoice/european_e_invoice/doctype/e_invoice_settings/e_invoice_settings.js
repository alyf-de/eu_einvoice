// Copyright (c) 2025, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("E Invoice Settings", {
	refresh(frm) {
		frm.trigger("setup_help_content");
		frm.trigger("setup_buttons");
		frm.trigger("setup_field_dependencies");
	},

	setup_help_content(frm) {
		// Populate the help HTML field with comprehensive documentation
		const help_html = `
			<div class="einvoice-help-content" style="padding: 15px;">
				<div class="alert alert-info">
					<strong>ℹ️ E-Invoice Settings</strong><br>
					Configure default settings for European e-invoicing (EN 16931, XRechnung, Factur-X/ZUGFeRD).
				</div>

				<h4 style="margin-top: 20px; border-bottom: 1px solid #d1d8dd; padding-bottom: 5px;">
					📚 Quick Links
				</h4>
				<ul>
					<li><a href="https://ec.europa.eu/digital-building-blocks/sites/display/DIGITAL/Obtaining+a+PEPPOL+Access+Point" target="_blank">PEPPOL Network Documentation</a></li>
					<li><a href="https://www.itb.ec.europa.eu/invoice/upload" target="_blank">EU E-Invoice Validator</a></li>
					<li><a href="https://www.xrechnung.de" target="_blank">XRechnung Specification (German)</a></li>
					<li><a href="https://fnfe-mpe.org/factur-x/" target="_blank">Factur-X/ZUGFeRD Specification</a></li>
					<li><a href="https://unece.org/trade/uncefact/xml-schemas" target="_blank">UN/CEFACT CII Standard</a></li>
				</ul>

				<h4 style="margin-top: 20px; border-bottom: 1px solid #d1d8dd; padding-bottom: 5px;">
					🎯 Profile Comparison
				</h4>
				<table class="table table-bordered table-sm">
					<thead>
						<tr style="background-color: #f8f9fa;">
							<th>Profile</th>
							<th>Use Case</th>
							<th>PDF Embedding</th>
							<th>Target Region</th>
						</tr>
					</thead>
					<tbody>
						<tr>
							<td><strong>BASIC</strong></td>
							<td>Simple invoices with minimal data</td>
							<td style="text-align: center;">✅</td>
							<td>France (mainly)</td>
						</tr>
						<tr>
							<td><strong>EN 16931</strong></td>
							<td>EU standard for cross-border invoicing</td>
							<td style="text-align: center;">✅</td>
							<td>All EU countries</td>
						</tr>
						<tr>
							<td><strong>EXTENDED</strong></td>
							<td>Complex invoices with additional data</td>
							<td style="text-align: center;">✅</td>
							<td>France (Factur-X)</td>
						</tr>
						<tr>
							<td><strong>XRECHNUNG</strong></td>
							<td>German public sector invoicing</td>
							<td style="text-align: center;">❌</td>
							<td>Germany (B2G)</td>
						</tr>
					</tbody>
				</table>

				<h4 style="margin-top: 20px; border-bottom: 1px solid #d1d8dd; padding-bottom: 5px;">
					⚙️ Settings Overview
				</h4>
				<div style="padding-left: 15px;">
					<p><strong>Defaults Tab:</strong> Configure default values for new invoices to reduce manual data entry.</p>
					<p><strong>PDF Settings Tab:</strong> Control PDF/A-3 conversion and XML embedding for ZUGFeRD/Factur-X compliance.</p>
					<p><strong>Import Settings Tab:</strong> Configure behavior when importing supplier e-invoices (automatic matching, validation strictness).</p>
				</div>

				<h4 style="margin-top: 20px; border-bottom: 1px solid #d1d8dd; padding-bottom: 5px;">
					🔧 Common Business Process URNs
				</h4>
				<ul style="font-family: monospace; font-size: 0.9em;">
					<li><code>urn:fdc:peppol.eu:2017:poacc:billing:01:1.0</code> - PEPPOL BIS Billing 3.0</li>
					<li><code>urn:cen.eu:en16931:2017</code> - EN 16931 Core</li>
					<li><code>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</code> - XRechnung 3.0</li>
					<li><code>urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic</code> - Factur-X BASIC</li>
				</ul>

				<h4 style="margin-top: 20px; border-bottom: 1px solid #d1d8dd; padding-bottom: 5px;">
					⚠️ Important Notes
				</h4>
				<div class="alert alert-warning" style="margin-top: 10px;">
					<ul style="margin-bottom: 0;">
						<li><strong>Ghostscript Required:</strong> PDF/A-3 conversion requires Ghostscript to be installed on your server.</li>
						<li><strong>XRechnung:</strong> Does not support PDF embedding - XML file must be sent separately.</li>
						<li><strong>Buyer Reference:</strong> Many public sector entities require this field (BT-10).</li>
						<li><strong>Schematron Caching:</strong> Keep enabled for better performance (up to 90% faster validation).</li>
					</ul>
				</div>

				<h4 style="margin-top: 20px; border-bottom: 1px solid #d1d8dd; padding-bottom: 5px;">
					🆘 Troubleshooting
				</h4>
				<details style="margin-top: 10px;">
					<summary style="cursor: pointer; color: #0089ff; font-weight: 500;">Validation fails with "Ghostscript not found"</summary>
					<div style="padding: 10px 0 10px 20px;">
						Install Ghostscript on your server:
						<pre style="background: #f8f9fa; padding: 10px; border-radius: 4px; margin-top: 5px;">sudo apt-get update && sudo apt-get install ghostscript</pre>
					</div>
				</details>
				<details style="margin-top: 10px;">
					<summary style="cursor: pointer; color: #0089ff; font-weight: 500;">Invoice validation is slow</summary>
					<div style="padding: 10px 0 10px 20px;">
						Make sure "Enable Schematron Caching" is enabled in the Advanced Validation Settings section.
					</div>
				</details>
				<details style="margin-top: 10px;">
					<summary style="cursor: pointer; color: #0089ff; font-weight: 500;">Imported invoice doesn't match Purchase Order</summary>
					<div style="padding: 10px 0 10px 20px;">
						Check that "Auto-match Purchase Orders" is enabled and the buyer reference in the e-invoice matches your PO number.
					</div>
				</details>
			</div>
		`;

		frm.set_df_property("help_html", "options", help_html);
	},

	setup_buttons(frm) {
		// Add custom buttons for useful actions
		if (!frm.is_new()) {
			// Add "Test Validation" button
			frm.add_custom_button(__("Test Validation"), () => {
				frm.trigger("show_test_validation_dialog");
			}, __("Tools"));

			// Add "View Documentation" button
			frm.add_custom_button(__("Open EU Validator"), () => {
				window.open("https://www.itb.ec.europa.eu/invoice/upload", "_blank");
			}, __("Tools"));
		}
	},

	setup_field_dependencies(frm) {
		// Show/hide fields based on dependencies
		frm.trigger("toggle_pdfa_fields");
	},

	enable_pdfa_conversion(frm) {
		frm.trigger("toggle_pdfa_fields");
	},

	toggle_pdfa_fields(frm) {
		// Toggle visibility of PDF/A related fields
		const pdfa_enabled = frm.doc.enable_pdfa_conversion;
		frm.toggle_display("pdfa_fallback_behavior", pdfa_enabled);
	},

	show_test_validation_dialog(frm) {
		const d = new frappe.ui.Dialog({
			title: __("Test E-Invoice Validation"),
			fields: [
				{
					label: __("Upload Test File"),
					fieldname: "test_file",
					fieldtype: "Attach",
					reqd: 1,
					description: __("Upload an XML or PDF file with embedded XML")
				},
				{
					fieldname: "column_break",
					fieldtype: "Column Break"
				},
				{
					label: __("Profile"),
					fieldname: "profile",
					fieldtype: "Select",
					options: ["BASIC", "EN 16931", "EXTENDED", "XRECHNUNG"],
					default: "EN 16931",
					reqd: 1
				}
			],
			size: "large",
			primary_action_label: __("Validate"),
			primary_action(values) {
				frappe.show_alert({
					message: __("Validation feature will be available in a future update"),
					indicator: "blue"
				});
				d.hide();
			}
		});
		d.show();
	},

	default_einvoice_profile(frm) {
		// Show info message when changing default profile
		if (frm.doc.default_einvoice_profile) {
			const profile_info = {
				"BASIC": __("BASIC profile: Minimal data, mainly used in France"),
				"EN 16931": __("EN 16931: EU standard, recommended for cross-border invoicing"),
				"EXTENDED": __("EXTENDED profile: Maximum data, Factur-X extended"),
				"XRECHNUNG": __("XRECHNUNG: German public sector, no PDF embedding")
			};

			if (profile_info[frm.doc.default_einvoice_profile]) {
				frappe.show_alert({
					message: profile_info[frm.doc.default_einvoice_profile],
					indicator: "blue"
				}, 5);
			}
		}
	},

	enable_schematron_caching(frm) {
		// Show performance hint
		if (!frm.doc.enable_schematron_caching) {
			frappe.msgprint({
				title: __("Performance Impact"),
				message: __("Disabling Schematron caching will significantly slow down validation. " +
					"XSL stylesheets (up to 4 MB) will be recompiled for every validation. " +
					"This is only recommended for debugging purposes."),
				indicator: "orange"
			});
		}
	},

	auto_create_supplier(frm) {
		// Warn about auto-creation
		if (frm.doc.auto_create_supplier) {
			frappe.msgprint({
				title: __("Auto-create Supplier"),
				message: __("Suppliers will be created automatically from imported e-invoices if not found. " +
					"Make sure to review and verify supplier data after import."),
				indicator: "blue"
			});
		}
	},

	auto_create_items(frm) {
		// Warn about auto-creation
		if (frm.doc.auto_create_items) {
			frappe.msgprint({
				title: __("Auto-create Items"),
				message: __("Items will be created automatically from seller product IDs if not found. " +
					"Make sure to review item master data and configure stock/accounting settings."),
				indicator: "blue"
			});
		}
	}
});
