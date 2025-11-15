// Copyright (c) 2025, ALYF GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("E Invoice Settings", {
	refresh(frm) {
		frm.trigger("setup_help_content");
		frm.trigger("setup_buttons");
		frm.trigger("setup_field_dependencies");
		frm.trigger("load_statistics");
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

			// Add "Clear Cache" button
			frm.add_custom_button(__("Clear Cache"), () => {
				frm.trigger("clear_cache");
			}, __("Tools"));

			// Add "Refresh Statistics" button
			frm.add_custom_button(__("Refresh Statistics"), () => {
				frm.trigger("load_statistics");
			}, __("Tools"));

			// Add "Download Sample" button
			frm.add_custom_button(__("Download Sample Invoice"), () => {
				window.open("/api/method/eu_einvoice.european_e_invoice.api.download_sample_invoice", "_blank");
			}, __("Tools"));

			// Add "View Documentation" button
			frm.add_custom_button(__("Open EU Validator"), () => {
				window.open("https://www.itb.ec.europa.eu/invoice/upload", "_blank");
			}, __("Tools"));
		}
	},

	load_statistics(frm) {
		// Load usage statistics and display in dashboard
		if (frm.is_new()) return;

		frm.call("get_statistics").then(r => {
			if (r.message) {
				frm.trigger("display_statistics", r.message);
			}
		});
	},

	display_statistics(frm, stats) {
		// Display statistics in a dashboard-style section
		const stats_html = `
			<div style="padding: 15px; background: #f8f9fa; border-radius: 8px; margin-bottom: 15px;">
				<h4 style="margin-top: 0; border-bottom: 2px solid #0089ff; padding-bottom: 10px;">
					📊 E-Invoice Statistics (Last 30 Days)
				</h4>
				<div class="row" style="margin-top: 15px;">
					<div class="col-sm-3">
						<div style="background: white; padding: 15px; border-radius: 4px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
							<div style="font-size: 32px; font-weight: bold; color: #0089ff;">${stats.total_generated}</div>
							<div style="color: #6c757d; margin-top: 5px;">Generated</div>
						</div>
					</div>
					<div class="col-sm-3">
						<div style="background: white; padding: 15px; border-radius: 4px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
							<div style="font-size: 32px; font-weight: bold; color: #28a745;">${stats.validation_passed}</div>
							<div style="color: #6c757d; margin-top: 5px;">Valid</div>
						</div>
					</div>
					<div class="col-sm-3">
						<div style="background: white; padding: 15px; border-radius: 4px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
							<div style="font-size: 32px; font-weight: bold; color: #dc3545;">${stats.validation_failed}</div>
							<div style="color: #6c757d; margin-top: 5px;">Failed</div>
						</div>
					</div>
					<div class="col-sm-3">
						<div style="background: white; padding: 15px; border-radius: 4px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
							<div style="font-size: 32px; font-weight: bold; color: #17a2b8;">${stats.total_imported}</div>
							<div style="color: #6c757d; margin-top: 5px;">Imported</div>
						</div>
					</div>
				</div>
				<div style="margin-top: 20px;">
					<strong>Success Rate:</strong> ${stats.success_rate}% |
					<strong>Cache Status:</strong> ${stats.cache_info.caching_enabled ? '✅ Enabled' : '❌ Disabled'}
					(${stats.cache_info.cache_size} stylesheets cached)
				</div>
				${stats.profile_breakdown && stats.profile_breakdown.length > 0 ? `
					<div style="margin-top: 15px;">
						<strong>Profile Distribution:</strong>
						<div style="margin-top: 10px;">
							${stats.profile_breakdown.map(p => `
								<div style="margin: 5px 0;">
									<span style="display: inline-block; width: 120px; font-weight: 500;">${p.einvoice_profile}:</span>
									<div style="display: inline-block; width: 200px; background: #e9ecef; border-radius: 3px; height: 20px; position: relative; vertical-align: middle;">
										<div style="background: #0089ff; height: 100%; width: ${(p.count / stats.total_generated * 100)}%; border-radius: 3px;"></div>
									</div>
									<span style="margin-left: 10px;">${p.count} (${Math.round(p.count / stats.total_generated * 100)}%)</span>
								</div>
							`).join('')}
						</div>
					</div>
				` : ''}
			</div>
		`;

		// Insert stats before the first tab
		const stats_wrapper = frm.fields_dict.sales_invoice_section.wrapper;
		const existing_stats = stats_wrapper.querySelector('.einvoice-stats');
		if (existing_stats) {
			existing_stats.remove();
		}

		const stats_div = document.createElement('div');
		stats_div.className = 'einvoice-stats';
		stats_div.innerHTML = stats_html;
		stats_wrapper.insertBefore(stats_div, stats_wrapper.firstChild);
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

	clear_cache(frm) {
		frappe.confirm(
			__("This will clear all cached Schematron stylesheets. The next validation will be slower but will use the latest XSL files. Continue?"),
			() => {
				frm.call("clear_validation_cache").then(r => {
					if (r.message) {
						// Reload statistics to show updated cache info
						frm.trigger("load_statistics");
					}
				});
			}
		);
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
				frappe.call({
					method: "eu_einvoice.european_e_invoice.api.test_validation",
					args: {
						test_file: values.test_file,
						profile: values.profile
					},
					callback: (r) => {
						if (r.message) {
							const result = r.message;

							if (result.success) {
								frappe.msgprint({
									title: __("✅ Validation Passed"),
									message: `
										<div style="color: #28a745; font-size: 16px; margin-bottom: 10px;">
											<strong>The e-invoice is valid!</strong>
										</div>
										<div>
											<strong>Profile:</strong> ${result.profile}<br>
											<strong>Errors:</strong> ${result.error_count}<br>
											<strong>Warnings:</strong> ${result.warning_count}
										</div>
										${result.warnings.length > 0 ? `
											<div style="margin-top: 15px;">
												<strong>Warnings:</strong>
												<ul>
													${result.warnings.map(w => `<li>${w}</li>`).join('')}
												</ul>
											</div>
										` : ''}
									`,
									indicator: "green"
								});
							} else {
								frappe.msgprint({
									title: __("❌ Validation Failed"),
									message: `
										<div style="color: #dc3545; font-size: 16px; margin-bottom: 10px;">
											<strong>The e-invoice contains errors</strong>
										</div>
										<div>
											<strong>Profile:</strong> ${result.profile}<br>
											<strong>Errors:</strong> ${result.error_count}<br>
											<strong>Warnings:</strong> ${result.warning_count}
										</div>
										<div style="margin-top: 15px;">
											<strong>Errors:</strong>
											<ul style="max-height: 300px; overflow-y: auto;">
												${result.errors.map(e => `<li>${e}</li>`).join('')}
											</ul>
										</div>
										${result.warnings.length > 0 ? `
											<div style="margin-top: 15px;">
												<strong>Warnings:</strong>
												<ul>
													${result.warnings.map(w => `<li>${w}</li>`).join('')}
												</ul>
											</div>
										` : ''}
									`,
									indicator: "red"
								});
							}
						}
					}
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
