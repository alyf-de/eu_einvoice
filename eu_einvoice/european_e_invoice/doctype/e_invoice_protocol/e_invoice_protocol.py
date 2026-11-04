# Copyright (c) 2026, ALYF GmbH and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class EInvoiceProtocol(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link | None
		content_hash: DF.Data | None
		copied_from: DF.Link | None
		detection_timestamp: DF.Datetime | None
		einvoice_format: DF.Data | None
		error_count: DF.Int
		file: DF.Link | None
		file_hash: DF.Data | None
		file_name: DF.Data | None
		invoice_profile: DF.Data | None
		message_count: DF.Int
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		result: DF.Literal["Pending", "Valid", "Invalid", "Unsupported"]
		ruleset_hash: DF.Data | None
		validation_errors: DF.Text | None
		validation_timestamp: DF.Datetime | None
		validation_warnings: DF.Text | None
		validator: DF.Data | None
		validator_version: DF.Data | None
		warning_count: DF.Int
	# end: auto-generated types

	def after_insert(self):
		# Stub created already settled (copied on upload) - still fill older Pending rows
		self._maybe_propagate_validation(force=True)

	def on_update(self):
		self._maybe_propagate_validation()

	def on_trash(self):
		# Protocol is in ignore_links_on_delete so Link checks skip copied_from; guard here.
		if frappe.db.exists("E Invoice Protocol", {"copied_from": self.name}):
			frappe.throw(
				_("Cannot delete {0} while other protocols still reference it via Copied From.").format(
					self.name
				)
			)

	def _maybe_propagate_validation(self, force: bool = False):
		"""When this protocol is settled, copy results onto Pending siblings (same hashes)."""
		if self.flags.get("skip_validation_propagate"):
			return
		if self.result not in ("Valid", "Invalid", "Unsupported"):
			return
		if not force and not (
			self.has_value_changed("result") or self.has_value_changed("validation_timestamp")
		):
			return

		from eu_einvoice.european_e_invoice.protocol import propagate_validation_to_pending_siblings

		propagate_validation_to_pending_siblings(self)


def on_doctype_update():
	# Upload reuse looks up by content_hash + file_hash + ruleset_hash.
	# One row per File is enforced by unique on `file` (DocType field).
	frappe.db.add_index("E Invoice Protocol", ["content_hash", "file_hash", "ruleset_hash"])


def has_permission(doc, ptype=None, user=None, debug=False):
	"""Deny protocol access unless the caller may read the linked File or reference.

	Controllers can only deny. Role DocPerm still grants Accounts read; this narrows
	it to rows whose File / reference document the user can already see. System
	Manager keeps full audit access (including orphaned rows after File delete).
	"""
	ptype = ptype or "read"
	# Do not tighten write/create/delete beyond DocPerm (Accounts have read only).
	if ptype not in ("read", "select", "export", "print", "email", "share"):
		return True

	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return True

	# Allow if either link is readable (Accounts may see the File while the
	# reference DocType is Purchase-only, and vice versa).
	if doc.reference_doctype and doc.reference_name:
		if frappe.db.exists(doc.reference_doctype, doc.reference_name):
			if frappe.has_permission(
				doc.reference_doctype,
				ptype="read",
				doc=doc.reference_name,
				user=user,
				debug=debug,
			):
				return True

	if doc.file and frappe.db.exists("File", doc.file):
		if frappe.has_permission("File", ptype="read", doc=doc.file, user=user, debug=debug):
			return True

	# No readable File or reference (deleted links, empty stubs): hide from Accounts.
	return False


def get_permission_query_conditions(user=None, **_kwargs):
	"""List only protocols whose linked File the user may already list.

	Reuses **File** permission query conditions so unattached private uploads stay
	owner-scoped, attached files follow the reference document, and empty *Company*
	rows are not broadly visible. System Manager sees all rows for audit.
	"""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return ""

	from frappe.core.doctype.file.file import (
		get_permission_query_conditions as get_file_permission_query_conditions,
	)

	file_conditions = get_file_permission_query_conditions(user)
	if not file_conditions:
		return ""

	return f"""(`tabE Invoice Protocol`.`file` IN (
		SELECT `tabFile`.`name` FROM `tabFile` WHERE {file_conditions}
	))"""
