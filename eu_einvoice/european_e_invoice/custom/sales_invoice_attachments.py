# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import frappe
from frappe import _
from frappe.core.doctype.file.utils import find_file_by_url
from frappe.utils import cstr

if TYPE_CHECKING:
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

	from eu_einvoice.european_e_invoice.doctype.e_invoice_settings.e_invoice_settings import (
		EInvoiceSettings,
	)

BULK_MIGRATE_LEGACY_EMBED_JOB_ID = "eu_einvoice.bulk_migrate_legacy_embed_attachments"
LEGACY_EMBED_CUSTOM_FIELD = "Sales Invoice-einvoice_embedded_document"
LEGACY_EMBED_FIELD = "einvoice_embedded_document"


@dataclass(frozen=True)
class EmbedAttachment:
	"""One embedded document resolved for CII ARD 916."""

	file: str
	file_name: str


def _first_duplicate_embed_filename(rows) -> tuple | None:
	"""Return the first pair of rows that share a normalized embed filename.

	Normalization matches typical MariaDB ``utf8mb4_unicode_ci`` unique-index behaviour,
	which is case-insensitive.
	"""
	seen_filenames: dict[str, object] = {}
	for row in rows:
		normalized_filename = cstr(row.file_name).lower()
		if normalized_filename in seen_filenames:
			return seen_filenames[normalized_filename], row
		seen_filenames[normalized_filename] = row
	return None


def _format_duplicate_embed_filename_message(first_row, duplicate_row) -> str:
	"""Build the user-facing message for one duplicate embed filename pair."""
	filename = first_row.file_name
	row_refs = ", ".join(_("row #{0}").format(row.idx) for row in (first_row, duplicate_row))
	return _("Embedded Documents {0} use the same filename. Each annex must have a unique filename.").format(
		f"{row_refs} ({filename})"
	)


DUPLICATE_EMBED_FILENAME_TITLE = _("Duplicate attachment filename")


def _raise_duplicate_embed_filename(first_row, duplicate_row) -> None:
	"""Raise the shared duplicate embed filename validation error."""
	frappe.throw(
		_format_duplicate_embed_filename_message(first_row, duplicate_row),
		title=DUPLICATE_EMBED_FILENAME_TITLE,
	)


def _validate_duplicate_embed_filenames(rows) -> None:
	"""Raise on the first duplicate embed filename in *rows*, if any."""
	duplicates = _first_duplicate_embed_filename(rows)
	if duplicates:
		_raise_duplicate_embed_filename(*duplicates)


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


def _get_legacy_embed_attachment(invoice: SalesInvoice) -> list[EmbedAttachment]:
	"""Return embedded documents from the legacy ``einvoice_embedded_document`` field.

	Args:
		invoice (SalesInvoice): Source invoice.

	Returns:
		list[EmbedAttachment]: Zero or one resolved attachment.

	Raises:
		frappe.ValidationError: When the legacy URL does not resolve to a **File** row.
	"""
	if not invoice.einvoice_embedded_document:
		return []

	file = find_file_by_url(invoice.einvoice_embedded_document)
	if not file:
		frappe.throw(
			_(
				"Could not embed attachment: no File record found for URL {0}. "
				"Check that the file exists and is attached to this document."
			).format(invoice.einvoice_embedded_document),
			title=_("Invalid attachment file"),
		)
	return [EmbedAttachment(file=file.name, file_name=file.file_name)]


def _get_table_embed_attachments(invoice: SalesInvoice) -> list[EmbedAttachment]:
	"""Return embedded documents from the ``einvoice_attachments`` child table.

	Validates duplicate filenames (BR-DE-22) and linked **File** rows before returning.

	Args:
		invoice (SalesInvoice): Source invoice.

	Returns:
		list[EmbedAttachment]: Attachments in child-table row order.

	Raises:
		frappe.ValidationError: When embed filenames are not unique or a **File** row
			does not exist.
	"""
	rows = list(invoice.get("einvoice_attachments") or [])
	if not rows:
		return []

	_validate_duplicate_embed_filenames(rows)

	attachments = []
	for row in rows:
		if not frappe.db.exists("File", row.file):
			frappe.throw(
				_(
					"Could not embed attachment: no File record found for '{0}' (ID {1}). "
					"Check that the file exists and is attached to this document."
				).format(row.file_name, row.file),
				title=_("Invalid attachment file"),
			)
		else:
			attachments.append(EmbedAttachment(file=row.file, file_name=row.file_name))
	return attachments


def get_embed_attachments(invoice: SalesInvoice) -> list[EmbedAttachment]:
	"""Resolve embedded documents for CII 916 embed on this invoice.

	Uses ``einvoice_attachments`` when **E Invoice Settings**
	``multi_attachment_embed_enabled`` is on; otherwise the legacy
	``einvoice_embedded_document`` field.

	Args:
		invoice (SalesInvoice): Source invoice.

	Returns:
		list[EmbedAttachment]: Attachments passed to ``EInvoiceGenerator._embed_attachments``.
	"""
	if frappe.db.get_single_value("E Invoice Settings", "multi_attachment_embed_enabled"):
		return _get_table_embed_attachments(invoice)
	return _get_legacy_embed_attachment(invoice)


def validate_einvoice_attachment_rows(invoice: SalesInvoice, settings: EInvoiceSettings) -> None:
	"""Validate **Embedded Documents** rows for duplicate filenames and content.

	Duplicate embed filenames (BR-DE-22) are detected case-insensitively
	(``str.lower()``), matching the DB unique index collation.

	On save/submit, duplicate-filename validation runs only when
	``settings.should_show_message`` is true — i.e. when **E Invoice Settings**
	has ``error_action_on_save`` or ``error_action_on_submit`` set. When no error
	action is configured, save proceeds without this check; the DB constraint and
	XML output path still enforce uniqueness. When the check runs, duplicates
	always block save with a specific message (no warn-only path).

	Duplicate ``content_hash`` values always produce an orange ``msgprint`` hint.

	Args:
		invoice (SalesInvoice): Invoice whose ``einvoice_attachments`` are checked.
		settings (EInvoiceSettings): **E Invoice Settings** single for error action.
	"""
	rows = invoice.get("einvoice_attachments")
	if not rows:
		return

	# Check for duplicate file names (BR-DE-22)
	if settings.should_show_message(invoice.docstatus):
		_validate_duplicate_embed_filenames(rows)

	# Check for duplicate content hashes
	hash_groups: dict[str, list] = defaultdict(list)

	for row in rows:
		content_hash = frappe.db.get_value("File", row.file, "content_hash")
		if content_hash:
			hash_groups[content_hash].append(row)

	for dup_rows in hash_groups.values():
		if len(dup_rows) > 1:
			row_labels = []
			for row in dup_rows:
				row_labels.append(_("row #{0} ({1})").format(row.idx, row.file_name))
			frappe.msgprint(
				_(
					"Embedded Documents {0} contain identical file content. Consider removing duplicate rows."
				).format(", ".join(row_labels)),
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
	invoice.append("einvoice_attachments", {"file": file.name, "file_name": file.file_name})
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
	fall back to the site-wide URL lookup. When submit sync creates a second
	**File** row for the same URL, prefer the row linked via the legacy attach
	field, then the oldest match.
	"""
	for filters in (
		{
			"file_url": file_url,
			"attached_to_doctype": invoice.doctype,
			"attached_to_name": invoice.name,
		},
		{"file_url": file_url},
	):
		candidates = []
		for file_name in frappe.get_all("File", filters=filters, pluck="name", order_by="creation asc"):
			file = frappe.get_doc("File", file_name)
			if file.is_downloadable():
				candidates.append(file)

		if candidates:
			for file in candidates:
				if file.attached_to_field == LEGACY_EMBED_FIELD:
					return file
			return candidates[0]

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
