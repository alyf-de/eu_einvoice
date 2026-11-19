"""Decode invoice XML bytes for APIs that need ``str`` (Saxon) without altering hashes."""

from __future__ import annotations

from lxml.etree import XMLParser, fromstring

# Same hardening as Import / format detection: no DTD / entity / network resolution.
_SAFE_XML_PARSER = XMLParser(
	resolve_entities=False,
	load_dtd=False,
	dtd_validation=False,
	no_network=True,
)


def decode_xml_bytes(xml_bytes: bytes) -> str:
	"""Return Unicode text using BOM / XML declaration encoding (default UTF-8).

	Keep hashing and storage on the original ``xml_bytes``. Only the Saxon /
	Schematron path needs a ``str``, and it must match the declared charset.
	"""
	if xml_bytes.startswith(b"\xef\xbb\xbf"):
		return xml_bytes.decode("utf-8-sig")
	if xml_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
		return xml_bytes.decode("utf-16")

	encoding = "utf-8"
	try:
		root = fromstring(xml_bytes, parser=_SAFE_XML_PARSER)
		declared = root.getroottree().docinfo.encoding
		if declared:
			encoding = declared
	except Exception:
		# Fall back to UTF-8; UnicodeDecodeError then surfaces to the caller.
		pass

	return xml_bytes.decode(encoding)
