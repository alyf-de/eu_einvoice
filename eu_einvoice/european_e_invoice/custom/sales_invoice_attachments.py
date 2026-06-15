# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.utils import cstr

if TYPE_CHECKING:
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

BULK_MIGRATE_LEGACY_EMBED_JOB_ID = "eu_einvoice.bulk_migrate_legacy_embed_attachments"
LEGACY_EMBED_CUSTOM_FIELD = "Sales Invoice-einvoice_embedded_document"


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
		error_message = _broken_legacy_embed_message(file_url)
		frappe.log_error(
			title=_(
				"Unable to migrate embedded document from legacy field `einvoice_embedded_document` "
				"to `einvoice_attachments` table for Sales Invoice {0}"
			).format(invoice.name),
			message=error_message,
			reference_doctype=invoice.doctype,
			reference_name=invoice.name,
		)
		return False

	invoice.append("einvoice_attachments", {"file": file.name})
	invoice.einvoice_embedded_document = ""

	if show_message:
		frappe.msgprint(
			_("The legacy embedded document was moved to the Embedded Documents table."),
			alert=True,
			indicator="orange",
		)

	return True


def _broken_legacy_embed_message(file_url: str) -> str:
	return _(
		"Could not migrate embedded document: no File record found for URL {0}. "
		"The legacy attachment link was left unchanged."
	).format(file_url)


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
		for file_data in frappe.get_all("File", filters=filters, fields="*"):
			file = frappe.get_doc(doctype="File", **file_data)
			if file.is_downloadable():
				return file

	return None


def set_legacy_embed_field_lockdown(enabled: bool) -> None:
	"""Hide and lock the legacy attach field when multi-embed is enabled."""
	if not frappe.db.exists("Custom Field", LEGACY_EMBED_CUSTOM_FIELD):
		return

	custom_field = frappe.get_doc("Custom Field", LEGACY_EMBED_CUSTOM_FIELD)
	custom_field.hidden = 1 if enabled else 0
	custom_field.read_only = 1 if enabled else 0
	custom_field.flags.ignore_permissions = True
	custom_field.save()
	frappe.clear_cache(doctype="Sales Invoice")


def bulk_migrate_legacy_embed_attachments() -> dict[str, int | list[tuple[str, str]]]:
	"""Background job: migrate legacy embed field on all Sales Invoices.

	Returns counts for migrated, already_migrated (cleared outside this job),
	broken legacy links (logged via ``migrate_legacy_embed_to_table``), plus
	unexpected ``errors`` from save failures.
	"""
	invoice_names = frappe.get_all(
		"Sales Invoice",
		filters={"einvoice_embedded_document": ("is", "set")},
		pluck="name",
	)

	migrated = 0
	already_migrated = 0
	broken = 0
	errors: list[tuple[str, str]] = []

	for invoice_name in invoice_names:
		try:
			invoice = frappe.get_doc("Sales Invoice", invoice_name)
			if migrate_legacy_embed_to_table(invoice, show_message=False):
				invoice.flags.ignore_permissions = True
				invoice.save()
				frappe.db.commit()
				migrated += 1  # success
				continue

			if invoice.einvoice_embedded_document:
				broken += 1  # broken legacy link
			else:
				already_migrated += 1  # legacy field cleared before this job processed the row
		except Exception as exc:
			errors.append((invoice_name, cstr(exc)))  # other unexpected errors
			frappe.log_error(
				title=_("Legacy embed migration failed for {0}").format(invoice_name),
				reference_doctype="Sales Invoice",
				reference_name=invoice_name,
				message=cstr(exc),
			)

	summary = _("Migrated {0} Sales Invoice(s). Already migrated {1}. Failed {2}.").format(
		migrated, already_migrated, broken + len(errors)
	)
	frappe.publish_realtime(
		"msgprint",
		{
			"message": summary,
			"alert": True,
			"indicator": "green" if not broken and not errors else "orange",
		},
		user=frappe.session.user,
	)

	return {
		"migrated": migrated,
		"already_migrated": already_migrated,
		"broken": broken,
		"errors": errors,
	}
