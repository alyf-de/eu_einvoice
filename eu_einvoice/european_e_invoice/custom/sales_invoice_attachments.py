# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _

SALES_INVOICE_ATTACHMENT_TABLE_FIELD = "einvoice_attachments"


def deduplicate_attachment_rows(doc) -> None:
	"""Collapse duplicate attachment ``file`` links; the first row for each file wins."""
	rows = doc.get(SALES_INVOICE_ATTACHMENT_TABLE_FIELD)
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
		doc.set(SALES_INVOICE_ATTACHMENT_TABLE_FIELD, unique_rows)
		frappe.msgprint(
			_(
				"{0} duplicate attachment row(s) were removed. "
				"The first row for each file was kept."
			).format(len(rows) - len(unique_rows)),
			alert=True,
			indicator="orange",
		)
