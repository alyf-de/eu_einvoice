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
	"""Custom Field flags for hiding/locking the legacy embed attach field."""
	if enabled is None:
		enabled = bool(frappe.db.get_single_value("E Invoice Settings", "multi_attachment_embed_enabled"))
	return {"hidden": 1 if enabled else 0, "read_only": 1 if enabled else 0}


def get_legacy_embed_attachment(invoice: SalesInvoice) -> list[str]:
	"""File URLs from the legacy ``einvoice_embedded_document`` field (0 or 1)."""
	if invoice.einvoice_embedded_document:
		return [invoice.einvoice_embedded_document]
	return []


def get_table_embed_attachments(invoice: SalesInvoice) -> list[str]:
	"""File URLs from ``einvoice_attachments``."""
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
	"""Resolve attachment file URLs for CII 916 embed (legacy field or attachment table)."""
	if frappe.db.get_single_value("E Invoice Settings", "multi_attachment_embed_enabled"):
		return get_table_embed_attachments(invoice)
	return get_legacy_embed_attachment(invoice)


def deduplicate_attachment_rows(invoice: SalesInvoice) -> None:
	"""Collapse duplicate attachment ``file`` links; the first row for each file wins."""
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

	Returns True when the legacy field was migrated. Returns False when there was
	nothing to migrate or the file link could not be resolved; in the latter case
	the legacy field is left unchanged and the failure is recorded.
	"""
	if not invoice.einvoice_embedded_document:
		return False

	file_url = invoice.einvoice_embedded_document
	file = _resolve_embed_file_for_invoice(invoice, file_url)
	if not file:
		_log_broken_legacy_embed(invoice, file_url)
		return False

	_persist_legacy_embed_migration_on_save(invoice, file)

	if show_message:
		frappe.msgprint(
			_("The legacy embedded document was moved to the Embedded Documents table."),
			alert=True,
			indicator="orange",
		)

	return True


def _log_broken_legacy_embed(invoice: SalesInvoice, file_url: str) -> None:
	frappe.log_error(
		title=_(
			"Unable to migrate embedded document from legacy field `einvoice_embedded_document` "
			"to `einvoice_attachments` table for Sales Invoice {0}"
		).format(invoice.name),
		message=_broken_legacy_embed_message(file_url),
		reference_doctype=invoice.doctype,
		reference_name=invoice.name,
	)


def _log_removed_broken_legacy_embed(invoice: SalesInvoice, file_url: str) -> None:
	title = _(
		"Removed broken legacy embedded document link from field `einvoice_embedded_document` "
		"for Sales Invoice {0}"
	).format(invoice.name)
	message = _broken_legacy_embed_removed_message(file_url)
	frappe.logger("eu_einvoice", allow_site=True).warning("%s — %s", title, message)


def _persist_legacy_embed_migration_on_save(invoice: SalesInvoice, file) -> None:
	invoice.append("einvoice_attachments", {"file": file.name})
	invoice.einvoice_embedded_document = ""


def _persist_legacy_embed_migration_db(invoice: SalesInvoice, file) -> None:
	_insert_attachment_row(invoice, file)
	frappe.db.set_value(
		"Sales Invoice",
		invoice.name,
		"einvoice_embedded_document",
		"",
		update_modified=True,
	)


def _insert_attachment_row(invoice: SalesInvoice, file) -> None:
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
	return _(
		"Could not migrate embedded document: no File record found for URL {0}. "
		"The legacy attachment link was left unchanged."
	).format(file_url)


def _broken_legacy_embed_removed_message(file_url: str) -> str:
	return _(
		"Could not migrate embedded document: no File record found for URL {0}. "
		"The legacy attachment link was removed."
	).format(file_url)


def _clear_legacy_embed_field(invoice: SalesInvoice) -> None:
	if invoice.docstatus == 0:
		invoice.einvoice_embedded_document = ""
		invoice.flags.ignore_permissions = True
		invoice.save()
	else:
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
	"""Hide and lock the legacy attach field when multi-embed is enabled."""
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
	"""Background job: migrate legacy embed field on Sales Invoices.

	When *include_submitted* is false, only draft invoices are processed via
	``save``. When true, submitted invoices are updated with direct child-row
	inserts and ``db.set_value`` so post-submit restrictions do not block migration.

	When *remove_broken_links* is true, unresolvable legacy URLs are cleared from
	``einvoice_embedded_document`` (logged) instead of left unchanged.

	Returns counts for migrated, already_migrated (cleared outside this job),
	broken legacy links (skipped), removed broken links, plus unexpected ``errors``.
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
