"""Alert system for E-Invoice validation failures."""

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime


def check_and_send_validation_failure_alerts():
	"""Check for repeated validation failures and send alerts if configured.

	This function should be called periodically (e.g., via a scheduled job).
	It checks for Sales Invoices with validation errors and sends email alerts
	if configured in E Invoice Settings.
	"""
	settings = frappe.get_single("E Invoice Settings")

	# Check if alerts are enabled
	if not settings.get("alert_on_validation_failure"):
		return

	if not settings.get("alert_email"):
		return

	# Get failed validations from last 24 hours
	last_24_hours = add_to_date(now_datetime(), hours=-24)

	failed_invoices = frappe.get_all(
		"Sales Invoice",
		filters={
			"einvoice_profile": ["!=", ""],
			"einvoice_is_correct": 0,
			"modified": [">=", last_24_hours],
			"docstatus": ["<", 2],  # Not cancelled
		},
		fields=["name", "customer", "posting_date", "grand_total", "einvoice_profile", "validation_errors"],
		limit=100,
	)

	if not failed_invoices:
		# No failures, no alert needed
		return

	# Send alert email
	send_validation_failure_alert(
		email=settings.alert_email,
		failed_invoices=failed_invoices,
		period_hours=24,
	)


def send_validation_failure_alert(email: str, failed_invoices: list[dict], period_hours: int = 24):
	"""Send email alert about validation failures.

	Args:
	    email: Email address to send alert to
	    failed_invoices: List of failed invoice documents
	    period_hours: Time period to mention in the email
	"""
	if not failed_invoices:
		return

	# Build email content
	subject = _("E-Invoice Validation Failures Alert - {0} invoices failed").format(len(failed_invoices))

	message = f"""
	<h3>E-Invoice Validation Failures</h3>
	<p>The following {len(failed_invoices)} invoice(s) have failed e-invoice validation in the last {period_hours} hours:</p>

	<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
		<thead>
			<tr style="background-color: #f8f9fa;">
				<th>Invoice</th>
				<th>Customer</th>
				<th>Date</th>
				<th>Amount</th>
				<th>Profile</th>
				<th>Errors (Preview)</th>
			</tr>
		</thead>
		<tbody>
	"""

	for invoice in failed_invoices[:20]:  # Limit to 20 in email
		error_preview = (invoice.validation_errors or "Unknown error")[:100]
		message += f"""
			<tr>
				<td><a href="{frappe.utils.get_url()}/app/sales-invoice/{invoice.name}">{invoice.name}</a></td>
				<td>{invoice.customer}</td>
				<td>{invoice.posting_date}</td>
				<td>{frappe.format_value(invoice.grand_total, {'fieldtype': 'Currency'})}</td>
				<td>{invoice.einvoice_profile}</td>
				<td>{error_preview}...</td>
			</tr>
		"""

	message += """
		</tbody>
	</table>
	"""

	if len(failed_invoices) > 20:
		message += f"""
		<p><em>... and {len(failed_invoices) - 20} more invoices.
		Please check the Sales Invoice list for details.</em></p>
		"""

	message += """
	<p><strong>Action Required:</strong> Please review and correct the validation errors in these invoices.</p>
	<p>You can disable these alerts in E Invoice Settings.</p>
	"""

	# Send email
	try:
		frappe.sendmail(
			recipients=[email],
			subject=subject,
			message=message,
			delayed=False,
		)

		# Log that alert was sent
		frappe.log_error(
			title=f"E-Invoice Validation Alert Sent ({len(failed_invoices)} failures)",
			message=f"Alert sent to {email}",
		)

	except Exception as e:
		frappe.log_error(
			title="Failed to send E-Invoice validation alert",
			message=f"Error: {str(e)}\nRecipient: {email}",
		)


def send_immediate_validation_failure_notification(invoice_name: str):
	"""Send immediate notification for critical validation failure.

	Args:
	    invoice_name: Name of the Sales Invoice that failed validation

	This can be called immediately when a submitted invoice fails validation.
	"""
	settings = frappe.get_single("E Invoice Settings")

	# Check if immediate notifications are enabled
	if not settings.get("alert_on_validation_failure"):
		return

	if not settings.get("alert_email"):
		return

	# Only send for submitted invoices with blocking errors
	invoice = frappe.get_doc("Sales Invoice", invoice_name)

	if invoice.docstatus != 1:  # Not submitted
		return

	if invoice.einvoice_is_correct:  # No errors
		return

	# Send email
	subject = _("URGENT: E-Invoice Validation Failed for {0}").format(invoice_name)

	message = f"""
	<h3 style="color: #dc3545;">⚠️ Critical E-Invoice Validation Failure</h3>

	<p><strong>Invoice:</strong> <a href="{frappe.utils.get_url()}/app/sales-invoice/{invoice.name}">{invoice.name}</a></p>
	<p><strong>Customer:</strong> {invoice.customer}</p>
	<p><strong>Amount:</strong> {frappe.format_value(invoice.grand_total, {'fieldtype': 'Currency'})}</p>
	<p><strong>Profile:</strong> {invoice.einvoice_profile}</p>

	<h4>Validation Errors:</h4>
	<pre style="background: #f8f9fa; padding: 10px; border-left: 4px solid #dc3545;">
{invoice.validation_errors}
	</pre>

	<p><strong>Action Required:</strong> This invoice has been submitted but the e-invoice validation failed.
	Please review and correct the errors immediately.</p>
	"""

	try:
		frappe.sendmail(
			recipients=[settings.alert_email],
			subject=subject,
			message=message,
			delayed=False,
		)
	except Exception as e:
		frappe.log_error(
			title="Failed to send immediate validation alert",
			message=f"Invoice: {invoice_name}\nError: {str(e)}",
		)
