"""Hardening helpers for untrusted XML before SaxonC / XSLT."""

from __future__ import annotations

_BOM = "\ufeff"
_DOCTYPE_MARK = "<!DOCTYPE"


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
		upper = xml_string.upper()
		start = upper.find(_DOCTYPE_MARK)
		if start == -1:
			return xml_string

		index = start + len(_DOCTYPE_MARK)
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
