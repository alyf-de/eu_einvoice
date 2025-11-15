"""Audit logging for E-Invoice operations."""

from typing import Literal

import frappe


def log_einvoice_event(
	reference_doctype: str,
	reference_name: str,
	action: Literal["Generated", "Validated", "Imported", "Exported", "Failed"],
	profile: str | None = None,
	duration_ms: int | None = None,
	errors: str | None = None,
	warnings: str | None = None,
):
	"""Log an e-invoice event for auditing and debugging.

	Args:
	    reference_doctype: The doctype of the document (e.g., "Sales Invoice")
	    reference_name: The name of the document (e.g., "SINV-00001")
	    action: The action that was performed
	    profile: The e-invoice profile used (BASIC, EN 16931, etc.)
	    duration_ms: Duration of the operation in milliseconds
	    errors: Error messages (if any)
	    warnings: Warning messages (if any)

	Example:
	    >>> log_einvoice_event(
	    ...     "Sales Invoice",
	    ...     "SINV-00001",
	    ...     "Validated",
	    ...     profile="EN 16931",
	    ...     duration_ms=250
	    ... )
	"""
	# Check if audit logging is enabled
	settings = frappe.get_single("E Invoice Settings")
	if not settings.get("enable_audit_log", True):  # Default to True
		return

	# Create audit log entry
	try:
		frappe.get_doc({
			"doctype": "Error Log",  # Reuse existing Error Log for now
			"method": f"E-Invoice: {action}",
			"error": _format_audit_message(
				reference_doctype=reference_doctype,
				reference_name=reference_name,
				action=action,
				profile=profile,
				duration_ms=duration_ms,
				errors=errors,
				warnings=warnings,
			),
		}).insert(ignore_permissions=True)
	except Exception:
		# Silently fail - audit logging should never break the main flow
		pass


def _format_audit_message(
	reference_doctype: str,
	reference_name: str,
	action: str,
	profile: str | None,
	duration_ms: int | None,
	errors: str | None,
	warnings: str | None,
) -> str:
	"""Format audit log message."""
	message = f"**Document:** {reference_doctype} - {reference_name}\n"
	message += f"**Action:** {action}\n"

	if profile:
		message += f"**Profile:** {profile}\n"

	if duration_ms is not None:
		message += f"**Duration:** {duration_ms}ms\n"

	if errors:
		message += f"\n**Errors:**\n{errors}\n"

	if warnings:
		message += f"\n**Warnings:**\n{warnings}\n"

	return message


def get_audit_logs(
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	action: str | None = None,
	limit: int = 100,
) -> list[dict]:
	"""Get audit logs for e-invoice operations.

	Args:
	    reference_doctype: Filter by document type
	    reference_name: Filter by document name
	    action: Filter by action type
	    limit: Maximum number of logs to return

	Returns:
	    List of audit log entries

	Example:
	    >>> logs = get_audit_logs(reference_doctype="Sales Invoice", limit=50)
	"""
	filters = {"method": ["like", "E-Invoice:%"]}

	if action:
		filters["method"] = ["like", f"E-Invoice: {action}%"]

	logs = frappe.get_all(
		"Error Log",
		filters=filters,
		fields=["name", "creation", "method", "error", "owner"],
		order_by="creation desc",
		limit=limit,
	)

	# Filter by reference if provided
	if reference_doctype or reference_name:
		filtered_logs = []
		for log in logs:
			if reference_doctype and reference_doctype not in log.error:
				continue
			if reference_name and reference_name not in log.error:
				continue
			filtered_logs.append(log)
		return filtered_logs

	return logs
