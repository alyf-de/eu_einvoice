"""Hardening helpers for untrusted XML before SaxonC / XSLT."""

from __future__ import annotations

import re

_BOM = "\ufeff"
# Search without uppercasing the whole string: characters like ß expand under
# .upper() / .casefold(), which shifts indices and can leave DOCTYPE intact.
_DOCTYPE_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)


def strip_xml_doctype(xml_string: str) -> str:
	"""Remove DOCTYPE declarations so SaxonC cannot resolve external entities.

	EN 16931 / XRechnung invoice XML does not need a DOCTYPE for visualization or
	Schematron validation. Stripping blocks XXE (local file read) and SSRF via
	``SYSTEM`` / ``PUBLIC`` entities that SaxonC would otherwise resolve on parse.
	"""
	if xml_string.startswith(_BOM):
		xml_string = xml_string[len(_BOM) :]

	# Scan repeatedly in case a payload embeds more than one DOCTYPE.
	while True:
		match = _DOCTYPE_RE.search(xml_string)
		if match is None:
			return xml_string

		start = match.start()
		index = match.end()
		bracket_depth = 0
		end = None
		while index < len(xml_string):
			char = xml_string[index]
			if char == "[":
				bracket_depth += 1
			elif char == "]":
				bracket_depth = max(0, bracket_depth - 1)
			elif char == ">" and bracket_depth == 0:
				end = index
				break
			index += 1

		if end is None:
			# Truncated / malformed DOCTYPE: drop from the marker onward so parse
			# cannot recover an external subset.
			return xml_string[:start]

		xml_string = xml_string[:start] + xml_string[end + 1 :]
