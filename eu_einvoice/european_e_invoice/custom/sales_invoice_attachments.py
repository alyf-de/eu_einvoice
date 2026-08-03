# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import frappe
from frappe import _
from frappe.core.doctype.file.utils import find_file_by_url
from frappe.utils import cint, cstr, get_link_to_form
from frappe.utils.background_jobs import create_job_id, enqueue

if TYPE_CHECKING:
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

	from eu_einvoice.european_e_invoice.doctype.e_invoice_settings.e_invoice_settings import (
		EInvoiceSettings,
	)

BULK_MIGRATE_LEGACY_EMBED_JOB_ID = "eu_einvoice.bulk_migrate_legacy_embed_attachments"
BULK_MIGRATE_PROGRESS_INTERVAL_SECONDS = 10
LEGACY_EMBED_CUSTOM_FIELD = "Sales Invoice-einvoice_embedded_document"
TABLE_EMBED_CUSTOM_FIELD = "Sales Invoice-einvoice_attachments"


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


def _raise_duplicate_embed_filename(first_row, duplicate_row) -> None:
	"""Raise the shared duplicate embed filename validation error."""
	frappe.throw(
		_format_duplicate_embed_filename_message(first_row, duplicate_row),
		title=_("Duplicate attachment filename"),
	)


def _validate_duplicate_embed_filenames(rows) -> None:
	"""Raise on the first duplicate embed filename in *rows*, if any."""
	duplicates = _first_duplicate_embed_filename(rows)
	if duplicates:
		_raise_duplicate_embed_filename(*duplicates)


def _raise_invalid_embed_file(display_name: str, file_id: str) -> None:
	"""Raise when a table annex **File** row is missing or not on *invoice*."""
	frappe.throw(
		_(
			"Could not embed attachment: no File record found for '{0}' (ID {1}). "
			"Check that the file exists and is attached to this document."
		).format(display_name, file_id),
		title=_("Invalid attachment file"),
	)


def _validate_embed_file_attached_to_invoice(invoice: SalesInvoice, file_id: str, display_name: str) -> None:
	"""Raise when *file_id* is missing or not attached to *invoice*."""
	attached_to = frappe.db.get_value(
		"File",
		file_id,
		("attached_to_doctype", "attached_to_name"),
	)
	if not attached_to:
		_raise_invalid_embed_file(display_name, file_id)

	attached_to_doctype, attached_to_name = attached_to
	if attached_to_doctype != invoice.doctype or attached_to_name != invoice.name:
		_raise_invalid_embed_file(display_name, file_id)


def _multi_attachment_embed_enabled(enabled: bool | None = None) -> bool:
	"""Resolve multi-embed enabled flag from *enabled* or **E Invoice Settings**."""
	if enabled is None:
		return bool(frappe.db.get_single_value("E Invoice Settings", "multi_attachment_embed_enabled"))
	return bool(enabled)


def legacy_embed_field_lockdown_properties(enabled: bool | None = None) -> dict[str, int]:
	"""Return ``hidden`` / ``read_only`` flags for the legacy embed custom field.

	When multi-embed is on, the legacy attach field is hidden and read-only.

	Args:
		enabled (bool, optional): When ``None``, reads
			``multi_attachment_embed_enabled`` from **E Invoice Settings**.

	Returns:
		dict[str, int]: ``{"hidden": 0|1, "read_only": 0|1}`` for **Custom Field** updates.
	"""
	enabled = _multi_attachment_embed_enabled(enabled)
	return {"hidden": 1 if enabled else 0, "read_only": 1 if enabled else 0}


def table_embed_field_visibility_properties(enabled: bool | None = None) -> dict[str, int]:
	"""Return ``hidden`` flag for the **Embedded Documents** table custom field.

	When multi-embed is off, the table is hidden so only the legacy attach field shows.
	When multi-embed is on, the table is visible (legacy is locked down separately).

	Args:
		enabled (bool, optional): When ``None``, reads
			``multi_attachment_embed_enabled`` from **E Invoice Settings**.

	Returns:
		dict[str, int]: ``{"hidden": 0|1}`` for **Custom Field** updates.
	"""
	enabled = _multi_attachment_embed_enabled(enabled)
	return {"hidden": 0 if enabled else 1, "read_only": 0 if enabled else 1}


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

	Validates duplicate filenames (BR-DE-22), invoice attachment ownership, and linked
	**File** rows before returning.

	Args:
		invoice (SalesInvoice): Source invoice.

	Returns:
		list[EmbedAttachment]: Attachments in child-table row order.

	Raises:
		frappe.ValidationError: When embed filenames are not unique, a **File** row
			does not exist, or the **File** is not attached to *invoice*.
	"""
	rows = list(invoice.get("einvoice_attachments") or [])
	if not rows:
		return []

	_validate_duplicate_embed_filenames(rows)

	attachments = []
	for row in rows:
		_validate_embed_file_attached_to_invoice(invoice, row.file, row.file_name)
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

	Raises:
		frappe.ValidationError: When multi-embed is on but the legacy attach field is still set.
	"""
	if frappe.db.get_single_value("E Invoice Settings", "multi_attachment_embed_enabled"):
		if invoice.einvoice_embedded_document:
			frappe.throw(
				_(
					"The Embedded Document has not been migrated to the Embedded Documents table yet. "
					"Wait for the background migration to finish or ask a System Manager to run "
					"Migrate attachments to table on {0}."
				).format(get_link_to_form("E Invoice Settings", "E Invoice Settings")),
				title=_("Legacy embed not migrated"),
			)
		return _get_table_embed_attachments(invoice)
	return _get_legacy_embed_attachment(invoice)


def validate_einvoice_attachment_rows(invoice: SalesInvoice, settings: EInvoiceSettings) -> None:
	"""Validate **Embedded Documents** rows for duplicate filenames, attachment ownership, and content.

	Duplicate embed filenames (BR-DE-22) are detected case-insensitively
	(``str.lower()``), matching the DB unique index collation, and always block
	save regardless of **E Invoice Settings** error-action configuration.

	Each row's **File** must be attached to *invoice* (``attached_to_doctype`` /
	``attached_to_name``). This mirrors the Desk Link query and blocks API/import
	paths that reference arbitrary **File** ids.

	Duplicate ``content_hash`` values always produce an orange ``msgprint`` hint.

	Args:
		invoice (SalesInvoice): Invoice whose ``einvoice_attachments`` are checked.
		settings (EInvoiceSettings): **E Invoice Settings** single (reserved for
			call-site compatibility; duplicate-filename checks do not use error actions).
	"""
	rows = invoice.get("einvoice_attachments")
	if not rows:
		return

	_validate_duplicate_embed_filenames(rows)

	for row in rows:
		_validate_embed_file_attached_to_invoice(invoice, row.file, row.file_name)

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
	"""Insert a child row and clear the legacy field via ``db.set_value``."""
	_insert_attachment_row(invoice, file)
	frappe.db.set_value(
		"Sales Invoice",
		invoice.name,
		"einvoice_embedded_document",
		"",
		update_modified=True,
	)


def _next_attachment_row_idx(invoice: SalesInvoice) -> int:
	"""Return the next ``idx`` for a new **E Invoice Attachment Row** on *invoice*."""
	current_max = frappe.db.sql(
		"""
		select coalesce(max(idx), 0)
		from `tabE Invoice Attachment Row`
		where parent=%s and parenttype=%s and parentfield=%s
		""",
		(invoice.name, invoice.doctype, "einvoice_attachments"),
	)[0][0]
	return int(current_max) + 1


def _insert_attachment_row(invoice: SalesInvoice, file) -> None:
	"""Insert one **E Invoice Attachment Row** linked to *invoice*."""
	frappe.get_doc(
		{
			"doctype": "E Invoice Attachment Row",
			"parent": invoice.name,
			"parenttype": invoice.doctype,
			"parentfield": "einvoice_attachments",
			"idx": _next_attachment_row_idx(invoice),
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
	child table, so we only consider **File** rows attached to *invoice*. When
	submit sync creates a second **File** row for the same URL, prefer the row
	linked via the legacy attach field, then the oldest match.
	"""
	candidates = []
	for file_name in frappe.get_all(
		"File",
		filters={
			"file_url": file_url,
			"attached_to_doctype": invoice.doctype,
			"attached_to_name": invoice.name,
		},
		pluck="name",
		order_by="creation asc",
	):
		file = frappe.get_doc("File", file_name)
		if file.is_downloadable():
			candidates.append(file)

	if candidates:
		for file in candidates:
			if file.attached_to_field == "einvoice_embedded_document":
				return file
		return candidates[0]


def _update_sales_invoice_custom_field(custom_field_name: str, properties: dict[str, int]) -> None:
	"""Update *properties* on a **Sales Invoice** **Custom Field** when it exists.

	No-op when the field is missing (e.g. **E Invoice Settings** ``on_update`` during
	``init_singles``, which runs before ``after_install`` creates custom fields).
	"""
	if not frappe.db.exists("Custom Field", custom_field_name):
		return

	custom_field = frappe.get_doc("Custom Field", custom_field_name)
	custom_field.update(properties)
	custom_field.flags.ignore_permissions = True
	custom_field.save()


def set_embed_attachment_field_exclusivity(enabled: bool) -> None:
	"""Apply exclusive Desk visibility for legacy attach vs **Embedded Documents** table.

	When *enabled* is ``True``, hide and lock the legacy field and show the table.
	When ``False``, show and unlock the legacy field and hide the table.

	Args:
		enabled (bool): Value of ``multi_attachment_embed_enabled``.
	"""
	_update_sales_invoice_custom_field(
		LEGACY_EMBED_CUSTOM_FIELD, legacy_embed_field_lockdown_properties(enabled)
	)
	_update_sales_invoice_custom_field(
		TABLE_EMBED_CUSTOM_FIELD, table_embed_field_visibility_properties(enabled)
	)
	frappe.clear_cache(doctype="Sales Invoice")


def sync_embed_attachment_field_exclusivity() -> None:
	"""Apply current ``multi_attachment_embed_enabled`` to legacy vs table field visibility."""
	enabled = _multi_attachment_embed_enabled()
	set_embed_attachment_field_exclusivity(enabled)


def queue_bulk_migrate_legacy_embed_attachments(
	*,
	remove_broken_links: bool = False,
	enqueue_after_commit: bool = False,
	user: str | None = None,
) -> dict[str, str | bool]:
	"""Enqueue site-wide legacy embed migration and notify the requesting user.

	Args:
		remove_broken_links (bool, optional): Clear unresolvable legacy URLs instead of
			skipping them.
		enqueue_after_commit (bool, optional): Defer enqueue until the current transaction
			commits (used when enabling multi-embed on **E Invoice Settings** save).
		user (str, optional): User to receive queue / progress / completion ``msgprint``
			messages. Defaults to ``frappe.session.user``.

	Returns:
		dict[str, str | bool]: ``job_id`` and ``queued`` (``False`` when deduplicated).
	"""
	notify_user = user or frappe.session.user
	namespaced_job_id = create_job_id(BULK_MIGRATE_LEGACY_EMBED_JOB_ID)
	job = enqueue(
		"eu_einvoice.european_e_invoice.custom.sales_invoice_attachments.bulk_migrate_legacy_embed_attachments",
		queue="long",
		timeout=1500,
		job_id=BULK_MIGRATE_LEGACY_EMBED_JOB_ID,
		deduplicate=True,
		enqueue_after_commit=enqueue_after_commit,
		remove_broken_links=cint(remove_broken_links),
		notify_user=notify_user,
	)

	if job:
		frappe.msgprint(
			_("Migration queued. Track progress in {0}.").format(get_link_to_form("RQ Job", job.id)),
			indicator="blue",
		)
		return {"job_id": job.id, "queued": True}

	frappe.msgprint(
		_("Migration already queued. Track progress in {0}.").format(
			get_link_to_form("RQ Job", namespaced_job_id)
		),
		indicator="orange",
	)
	return {"job_id": namespaced_job_id, "queued": False}


def bulk_migrate_legacy_embed_attachments(
	remove_broken_links: bool = False,
	notify_user: str | None = None,
) -> dict[str, int | list[tuple[str, str]]]:
	"""Migrate legacy ``einvoice_embedded_document`` values site-wide (background job).

	All successful migrations (draft, submitted, or cancelled) persist via direct DB
	writes so unrelated **Sales Invoice** validation cannot block the job.

	Args:
		remove_broken_links (bool, optional): When ``True``, clear unresolvable legacy
			URLs instead of skipping them. Removals are logged at site warning level.
		notify_user (str, optional): User to receive progress and completion ``msgprint``
			messages via realtime events.

	Returns:
		dict: Counts with keys ``migrated``, ``already_migrated``, ``broken``, ``removed``,
			and ``errors`` (list of ``(invoice_name, message)`` tuples for unexpected
			exceptions). Publishes realtime ``msgprint`` updates to *notify_user* when set.
	"""
	invoice_names = frappe.get_all(
		"Sales Invoice",
		filters={"einvoice_embedded_document": ("is", "set")},
		pluck="name",
	)
	total = len(invoice_names)

	migrated = 0
	already_migrated = 0
	broken = 0
	removed = 0
	errors: list[tuple[str, str]] = []
	last_progress_at = time.monotonic()

	for processed, invoice_name in enumerate(invoice_names, start=1):
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
				_persist_legacy_embed_migration_db(invoice, file)
				migrated += 1
				persisted = True

			if persisted:
				# Per-invoice commit in bulk worker; partial progress survives later failures.
				frappe.db.commit()  # nosemgrep
		except Exception as exc:
			frappe.db.rollback()
			errors.append((invoice_name, cstr(exc)))
			frappe.log_error(
				title=_("Legacy embed migration failed for {0}").format(invoice_name),
				reference_doctype="Sales Invoice",
				reference_name=invoice_name,
			)

		if notify_user:
			now = time.monotonic()
			if now - last_progress_at >= BULK_MIGRATE_PROGRESS_INTERVAL_SECONDS:
				frappe.publish_realtime(
					"msgprint",
					{
						"message": _(
							"Legacy embed migration in progress: {0} of {1} invoices processed."
						).format(processed, total),
						"indicator": "blue",
					},
					user=notify_user,
				)
				last_progress_at = now

	summary = _format_bulk_migration_summary(
		migrated=migrated,
		already_migrated=already_migrated,
		broken=broken,
		removed=removed,
		errors=errors,
	)
	if notify_user:
		frappe.publish_realtime(
			"msgprint",
			{
				"message": _("{0} Migration finished.").format(summary),
				"indicator": "green" if not broken and not removed and not errors else "orange",
			},
			user=notify_user,
		)

	return {
		"migrated": migrated,
		"already_migrated": already_migrated,
		"broken": broken,
		"removed": removed,
		"errors": errors,
	}
