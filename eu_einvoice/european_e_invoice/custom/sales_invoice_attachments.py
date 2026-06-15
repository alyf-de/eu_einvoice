# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _

if TYPE_CHECKING:
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice


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
			_(
				"{0} duplicate attachment row(s) were removed. "
				"The first row for each file was kept."
			).format(len(rows) - len(unique_rows)),
			alert=True,
			indicator="orange",
		)
