# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class EInvoiceAttachmentRow(Document):
	"""Child row for embedded annex files on **Sales Invoice**.

	``file_name`` (fetched from **File**) is the CII/XML ``@filename`` (BR-DE-22).
	``display_name`` is optional and for human-readable print output only.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		display_name: DF.Data | None
		file: DF.Link
		file_name: DF.Data
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
	# end: auto-generated types

	pass


def on_doctype_update():
	"""Apply DB uniqueness for embed filenames scoped to the parent invoice.

	The composite unique index on ``(parent, file_name)`` compares ``file_name``
	using the database collation (MariaDB ``utf8mb4_unicode_ci``: case-insensitive).
	Python validators use ``str.lower()`` for parity. ``file_name`` is required and
	not nullable on the child table.
	"""
	frappe.db.add_unique(
		"E Invoice Attachment Row",
		["parent", "file_name"],
		constraint_name="unique_parent_file_name",
	)
