from erpnext.edi.doctype.code_list.code_list import get_codes_for, get_default_code


class CommonCodeRetriever:
	"""Retrieve a common code from a list of code lists and a list of records."""

	def __init__(self, code_lists: list[str], default_code: str):
		self.code_lists = code_lists
		self.default_code = default_code

	def get(self, records: list[tuple[str, str]]) -> str | None:
		return self.get_code(records) or self.get_default_code() or self.default_code

	def get_code(self, records: list[tuple[str, str]]) -> str | None:
		"""Find a common code from a given list of records."""
		codes = None
		for code_list in self.code_lists:
			for doctype, name in records:
				if not name:
					continue

				codes = get_codes_for(code_list, doctype, name)
				if codes:
					break

		return codes[0] if codes else None

	def get_default_code(self) -> str | None:
		"""Find the default common code from the list of code lists."""
		for code_list in self.code_lists:
			default_code = get_default_code(code_list)
			if default_code:
				return default_code

		return None
