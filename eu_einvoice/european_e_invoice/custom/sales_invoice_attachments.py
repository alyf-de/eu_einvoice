# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import frappe
from frappe import _
from frappe.utils import cstr

if TYPE_CHECKING:
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

BULK_MIGRATE_LEGACY_EMBED_JOB_ID = "eu_einvoice.bulk_migrate_legacy_embed_attachments"
LEGACY_EMBED_CUSTOM_FIELD = "Sales Invoice-einvoice_embedded_document"


def legacy_embed_field_lockdown_properties(enabled: bool | None = None) -> dict[str, int]:
	"""Return ``hidden`` / ``read_only`` flags for the legacy embed custom field.

	Args:
		enabled (bool, optional): When ``None``, reads
			``multi_attachment_embed_enabled`` from **E Invoice Settings**.

	Returns:
		dict[str, int]: ``{"hidden": 0|1, "read_only": 0|1}`` for **Custom Field** updates.
	"""
	if enabled is None:
		enabled = bool(frappe.db.get_single_value("E Invoice Settings", "multi_attachment_embed_enabled"))
	return {"hidden": 1 if enabled else 0, "read_only": 1 if enabled else 0}


def get_legacy_embed_attachment(invoice: SalesInvoice) -> list[str]:
	"""Return file URLs from the legacy ``einvoice_embedded_document`` field.

	Args:
		invoice (SalesInvoice): Source invoice.

	Returns:
		list[str]: Zero or one ``file_url`` string.
	"""
	if invoice.einvoice_embedded_document:
		return [invoice.einvoice_embedded_document]
	return []


def get_table_embed_attachments(invoice: SalesInvoice) -> list[str]:
	"""Return file URLs from the ``einvoice_attachments`` child table.

	Args:
		invoice (SalesInvoice): Source invoice.

	Returns:
		list[str]: ``file_url`` values in child-table row order.

	Raises:
		frappe.ValidationError: When a linked **File** row has no ``file_url``.
	"""
	rows = invoice.get("einvoice_attachments")
	if not rows:
		return []

	urls = []
	for row in rows:
		file_url = frappe.db.get_value("File", row.file, "file_url")
		if file_url:
			urls.append(file_url)
		else:
			frappe.throw(
				_(
					"Could not embed attachment: no file URL found for File '{0}' (ID {1}). "
					"Check that the file exists and is attached to this document."
				).format(row.file_name, row.file),
				title=_("Invalid attachment file"),
			)
	return urls


def get_embed_attachments(invoice: SalesInvoice) -> list[str]:
	"""Resolve attachment file URLs for CII 916 embed on this invoice.

	Uses ``einvoice_attachments`` when **E Invoice Settings**
	``multi_attachment_embed_enabled`` is on; otherwise the legacy
	``einvoice_embedded_document`` field.

	Args:
		invoice (SalesInvoice): Source invoice.

	Returns:
		list[str]: ``file_url`` strings passed to ``EInvoiceGenerator._embed_attachments``.
	"""
	if frappe.db.get_single_value("E Invoice Settings", "multi_attachment_embed_enabled"):
		return get_table_embed_attachments(invoice)
	return get_legacy_embed_attachment(invoice)


def deduplicate_attachment_rows(invoice: SalesInvoice) -> None:
	"""Collapse duplicate ``file`` links in ``einvoice_attachments``.

	The first row for each **File** link is kept. Shows an orange ``msgprint`` when
	rows are removed.

	Args:
		invoice (SalesInvoice): Invoice whose child table may be mutated in place.
	"""
	rows = invoice.get("einvoice_attachments")
	if not rows:
		return

	seen_files: set[str] = set()
	unique_rows = []
	for row in rows:
		if row.file in seen_files:
			continue
		seen_files.add(row.file)
		unique_rows.append(row)

	if len(unique_rows) < len(rows):
		invoice.set("einvoice_attachments", unique_rows)
		frappe.msgprint(
			_("{0} duplicate attachment row(s) were removed. The first row for each file was kept.").format(
				len(rows) - len(unique_rows)
			),
			alert=True,
			indicator="orange",
		)


def migrate_legacy_embed_to_table(
	invoice: SalesInvoice,
	*,
	show_message: bool = True,
) -> bool:
	"""Move ``einvoice_embedded_document`` into ``einvoice_attachments`` when multi-embed is on.

	Args:
		invoice (SalesInvoice): Invoice being saved or validated.
		show_message (bool, optional): When ``True``, show orange ``msgprint`` on success
			or when the legacy URL cannot be resolved.

	Returns:
		bool: ``True`` when the legacy field was migrated; ``False`` when there was nothing
			to migrate or the file link could not be resolved. On failure the legacy field
			is left unchanged and the failure is logged to **Error Log**.
	"""
	if not invoice.einvoice_embedded_document:
		return False

	file_url = invoice.einvoice_embedded_document
	file = _resolve_embed_file_for_invoice(invoice, file_url)
	if file:
		_persist_legacy_embed_migration_on_save(invoice, file)
		if show_message:
			frappe.msgprint(
				_("The legacy embedded document was moved to the Embedded Documents table."),
				alert=True,
				indicator="orange",
			)
		return True

	else:
		_log_broken_legacy_embed(invoice, file_url)
		if show_message:
			frappe.msgprint(
				_(
					"Could not migrate Embedded Document ({0}): no File record found for URL {1}. "
					"The link was left unchanged. Ask a System Manager to run "
					"Migrate attachments to table on E Invoice Settings."
				).format("einvoice_embedded_document", file_url),
				alert=True,
				indicator="orange",
			)
		return False


def _log_broken_legacy_embed(invoice: SalesInvoice, file_url: str) -> None:
	"""Write an **Error Log** entry for an unresolvable legacy embed URL."""
	frappe.log_error(
		title=_(
			"Unable to migrate embedded document from legacy field `einvoice_embedded_document` "
			"to `einvoice_attachments` table for Sales Invoice {0}"
		).format(invoice.name),
		message=_broken_legacy_embed_message(file_url),
		reference_doctype=invoice.doctype,
		reference_name=invoice.name,
	)


def _persist_legacy_embed_migration_on_save(invoice: SalesInvoice, file) -> None:
	"""Append a child row and clear ``einvoice_embedded_document`` on an in-memory invoice."""
	invoice.append("einvoice_attachments", {"file": file.name})
	invoice.einvoice_embedded_document = ""


def _persist_legacy_embed_migration_db(invoice: SalesInvoice, file) -> None:
	"""Insert a child row and clear the legacy field via ``db.set_value`` (submitted docs)."""
	_insert_attachment_row(invoice, file)
	frappe.db.set_value(
		"Sales Invoice",
		invoice.name,
		"einvoice_embedded_document",
		"",
		update_modified=True,
	)


def _insert_attachment_row(invoice: SalesInvoice, file) -> None:
	"""Insert one **E Invoice Attachment Row** linked to *invoice*."""
	frappe.get_doc(
		{
			"doctype": "E Invoice Attachment Row",
			"parent": invoice.name,
			"parenttype": invoice.doctype,
			"parentfield": "einvoice_attachments",
			"file": file.name,
			"file_name": file.file_name,
		}
	).insert(ignore_permissions=True)


def _broken_legacy_embed_message(file_url: str) -> str:
	"""Return the **Error Log** message body for a skipped broken legacy URL."""
	return _(
		"Could not migrate embedded document: no File record found for URL {0}. "
		"The legacy attachment link was left unchanged."
	).format(file_url)


def _broken_legacy_embed_removed_message(file_url: str) -> str:
	"""Return the site log message body when a broken legacy URL is cleared."""
	return _(
		"Could not migrate embedded document: no File record found for URL {0}. "
		"The legacy attachment link was removed."
	).format(file_url)


def _clear_legacy_embed_field(invoice: SalesInvoice) -> None:
	"""Clear ``einvoice_embedded_document`` on *invoice* via ``db.set_value``."""
	frappe.db.set_value(
		"Sales Invoice",
		invoice.name,
		"einvoice_embedded_document",
		"",
		update_modified=True,
	)


def _handle_broken_legacy_embed(
	invoice: SalesInvoice,
	file_url: str,
	*,
	remove_broken_links: bool,
) -> Literal["broken", "removed"]:
	"""Handle an unresolvable legacy URL during bulk migration."""
	if remove_broken_links:
		_clear_legacy_embed_field(invoice)

		title = _(
			"Removed broken legacy embedded document link from field `einvoice_embedded_document` "
			"for Sales Invoice {0}"
		).format(invoice.name)
		message = _broken_legacy_embed_removed_message(file_url)
		frappe.logger("eu_einvoice", allow_site=True).warning("%s — %s", title, message)
		return "removed"
	else:
		_log_broken_legacy_embed(invoice, file_url)
		return "broken"


def _format_bulk_migration_summary(
	*,
	migrated: int,
	already_migrated: int,
	broken: int,
	removed: int,
	errors: list[tuple[str, str]],
) -> str:
	"""Build the user-facing summary string for a bulk migration run."""
	parts = [_("Migrated {0} Sales Invoice(s).").format(migrated)]
	if already_migrated:
		parts.append(_("Already migrated {0}.").format(already_migrated))
	if broken:
		parts.append(_("Broken file links (skipped): {0}.").format(broken))
	if removed:
		parts.append(_("Broken file links (removed): {0}.").format(removed))
	if errors:
		parts.append(_("Errors: {0}.").format(len(errors)))
	return " ".join(parts)


def _resolve_embed_file_for_invoice(invoice: SalesInvoice, file_url: str):
	"""Resolve a legacy embed URL to a **File** row for this invoice.

	``find_file_by_url`` returns the first downloadable **File** for a URL
	site-wide and is meant for download access, not for deciding which row
	belongs on a specific document. When several **File** rows share the same
	``file_url`` (re-uploads, copies, stale rows), it can pick the wrong one.

	Migration must move *this* invoice's legacy attach into *this* invoice's
	child table, so we prefer a **File** attached to the invoice and only then
	fall back to the site-wide URL lookup.
	"""
	for filters in (
		{
			"file_url": file_url,
			"attached_to_doctype": invoice.doctype,
			"attached_to_name": invoice.name,
		},
		{"file_url": file_url},
	):
		for file_name in frappe.get_all("File", filters=filters, pluck="name"):
			file = frappe.get_doc("File", file_name)
			if file.is_downloadable():
				return file

	return None


def set_legacy_embed_field_lockdown(enabled: bool) -> None:
	"""Hide and lock ``einvoice_embedded_document`` when multi-embed is enabled.

	Args:
		enabled (bool): When ``True``, set the legacy **Custom Field** to hidden and
			read-only; when ``False``, show and unlock it.
	"""
	if not frappe.db.exists("Custom Field", LEGACY_EMBED_CUSTOM_FIELD):
		return

	custom_field = frappe.get_doc("Custom Field", LEGACY_EMBED_CUSTOM_FIELD)
	custom_field.update(legacy_embed_field_lockdown_properties(enabled))
	custom_field.flags.ignore_permissions = True
	custom_field.save()
	frappe.clear_cache(doctype="Sales Invoice")


def bulk_migrate_legacy_embed_attachments(
	include_submitted: bool = False,
	remove_broken_links: bool = False,
) -> dict[str, int | list[tuple[str, str]]]:
	"""Migrate legacy ``einvoice_embedded_document`` values site-wide (background job).

	Args:
		include_submitted (bool, optional): When ``True``, also update submitted and
			cancelled invoices via direct DB writes. When ``False``, only draft invoices
			are migrated through ``save``.
		remove_broken_links (bool, optional): When ``True``, clear unresolvable legacy
			URLs instead of skipping them. Removals are logged at site warning level.

	Returns:
		dict: Counts with keys ``migrated``, ``already_migrated``, ``broken``, ``removed``,
			and ``errors`` (list of ``(invoice_name, message)`` tuples for unexpected
			exceptions). Publishes a realtime ``msgprint`` summary to the enqueueing user.
	"""
	filters: dict = {"einvoice_embedded_document": ("is", "set")}
	if not include_submitted:
		filters["docstatus"] = 0

	invoice_names = frappe.get_all("Sales Invoice", filters=filters, pluck="name")

	migrated = 0
	already_migrated = 0
	broken = 0
	removed = 0
	errors: list[tuple[str, str]] = []

	for invoice_name in invoice_names:
		try:
			invoice = frappe.get_doc("Sales Invoice", invoice_name)
			legacy_url = invoice.einvoice_embedded_document
			if not legacy_url:
				already_migrated += 1
				continue

			persisted = False
			file = _resolve_embed_file_for_invoice(invoice, legacy_url)
			if not file:
				outcome = _handle_broken_legacy_embed(
					invoice, legacy_url, remove_broken_links=remove_broken_links
				)
				if outcome == "removed":
					removed += 1
					persisted = True
				else:
					broken += 1
			else:
				if invoice.docstatus == 0:
					_persist_legacy_embed_migration_on_save(invoice, file)
					invoice.flags.ignore_permissions = True
					invoice.save()
				else:
					_persist_legacy_embed_migration_db(invoice, file)

				migrated += 1
				persisted = True

			if persisted:
				# Per-invoice commit in bulk worker; partial progress survives later failures.
				frappe.db.commit()  # nosemgrep
		except Exception as exc:
			errors.append((invoice_name, cstr(exc)))
			frappe.log_error(
				title=_("Legacy embed migration failed for {0}").format(invoice_name),
				reference_doctype="Sales Invoice",
				reference_name=invoice_name,
				message=cstr(exc),
			)

	summary = _format_bulk_migration_summary(
		migrated=migrated,
		already_migrated=already_migrated,
		broken=broken,
		removed=removed,
		errors=errors,
	)
	frappe.publish_realtime(
		"msgprint",
		{
			"message": summary,
			"alert": True,
			"indicator": "green" if not broken and not removed and not errors else "orange",
		},
		user=frappe.session.user,
	)

	return {
		"migrated": migrated,
		"already_migrated": already_migrated,
		"broken": broken,
		"removed": removed,
		"errors": errors,
	}
