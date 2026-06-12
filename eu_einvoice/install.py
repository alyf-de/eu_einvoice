from pathlib import Path

import frappe
from erpnext.edi.doctype.code_list.code_list_import import (
	import_genericode_content,
	parse_genericode_content,
)
from erpnext.edi.doctype.common_code.common_code import import_genericode
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from .custom_fields import get_custom_fields

CODELIST_DIR = Path(__file__).parent / "codelist"
# All EN 16931 genericode files published by the EU share this ColumnSet.
CODELIST_COLUMNS = {"code": "Code", "title": "Name", "description": "Remark"}


def after_install():
	make_custom_fields()
	import_code_lists()


def make_custom_fields():
	create_custom_fields(get_custom_fields())


def import_code_lists():
	"""Import the bundled EN 16931 code lists as Code List and Common Code records."""
	paths = sorted(CODELIST_DIR.glob("*.gc"))
	for i, path in enumerate(paths, start=1):
		frappe.publish_progress(i / len(paths) * 100, title=_("Importing Code Lists"), description=path.stem)

		content = path.read_bytes()
		# Code Lists are named after their CanonicalVersionUri, so this skips
		# already imported versions and picks up newly bundled ones.
		version_uri = parse_genericode_content(content).findtext(".//CanonicalVersionUri")
		if frappe.db.exists("Code List", version_uri):
			continue

		result = import_genericode_content(
			doctype="Code List",
			docname=None,
			content=content,
			file_name=path.name,
		)
		# import_genericode reports its own progress from 0 to 100 % per code list, which
		# resets the dialog above for every file. A task id routes those events to a task
		# room that nobody listens to, leaving one continuous progress bar.
		frappe.local.task_id = "eu_einvoice_code_list_import"
		try:
			import_genericode(result["code_list"], result["file"], CODELIST_COLUMNS)
		finally:
			frappe.local.task_id = None


def before_tests():
	import frappe

	from eu_einvoice.tests.scaffold import ensure_embed_test_masters

	ensure_embed_test_masters()
	frappe.db.commit()
