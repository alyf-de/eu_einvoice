from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from lxml import objectify

from eu_einvoice.saxon import get_saxon_version, transform_to_string
from eu_einvoice.utils import EInvoiceProfile

# Re-export for callers / tests that still import from schematron
__all__ = [
	"PROFILE_TO_XSL",
	"extract_failed_asserts",
	"get_errors_from_stylesheet",
	"get_ruleset_hash",
	"get_saxon_version",
	"get_validation_errors",
	"get_validation_report",
	"get_validator_version",
	"stylesheets_for_profile",
]

SCHEMATRON_DIR = Path(__file__).parent

# Bundled stylesheet file names. Exact ruleset pins live in README.md and in
# ruleset_hash (SHA-256 of these files) - do not keep a second hand-written version table.
PROFILE_TO_XSL = {
	EInvoiceProfile.BASIC: "FACTUR-X_BASIC.xsl",
	EInvoiceProfile.EN16931: "EN16931-CII-validation-preprocessed.xsl",
	EInvoiceProfile.EXTENDED: "FACTUR-X_EXTENDED.xsl",
	EInvoiceProfile.XRECHNUNG: "XRechnung-CII-validation.xsl",
}


def get_validation_errors(xml_string: str, profile: EInvoiceProfile):
	return get_errors_from_stylesheet(xml_string, PROFILE_TO_XSL[profile])


def get_validator_version(profile: EInvoiceProfile | str | None = None) -> str:
	"""Stylesheet file name(s) + SaxonC version for audit logs / protocol.

	Exact ruleset identity is ``ruleset_hash``; this string only names which
	bundled files ran so Schematron updates need not touch a second version map.
	"""
	names = stylesheets_for_profile(profile)
	ruleset = ", ".join(names) if names else "Schematron (bundled)"
	return f"{ruleset}; {get_saxon_version()}"


def stylesheets_for_profile(profile: EInvoiceProfile | str | None) -> tuple[str, ...]:
	"""Return bundled stylesheet names applied for this profile (order matters for hashing)."""
	if profile is None:
		return ()
	try:
		profile_enum = profile if isinstance(profile, EInvoiceProfile) else EInvoiceProfile(profile)
	except ValueError:
		return ()

	names: list[str] = []
	primary = PROFILE_TO_XSL.get(profile_enum)
	if primary:
		names.append(primary)
	# XRechnung validation also runs EN 16931
	if profile_enum == EInvoiceProfile.XRECHNUNG:
		en16931 = PROFILE_TO_XSL.get(EInvoiceProfile.EN16931)
		if en16931 and en16931 not in names:
			names.append(en16931)
	return tuple(names)


def get_ruleset_hash(profile: EInvoiceProfile | str | None = None) -> str:
	"""SHA-256 of the stylesheet bytes used for this profile (empty when none apply)."""
	if profile is None:
		return ""
	key = profile.value if isinstance(profile, EInvoiceProfile) else str(profile)
	return _ruleset_hash_for_profile_key(key)


@lru_cache(maxsize=16)
def _ruleset_hash_for_profile_key(profile: str) -> str:
	names = stylesheets_for_profile(profile)
	if not names:
		return ""
	hasher = hashlib.sha256()
	for name in names:
		hasher.update((SCHEMATRON_DIR / name).read_bytes())
	return hasher.hexdigest()


def get_errors_from_stylesheet(xml_string: str, stylesheet: str):
	stylesheet_path = SCHEMATRON_DIR / stylesheet
	report = get_validation_report(xml_string, str(stylesheet_path))
	return extract_failed_asserts(report)


def extract_failed_asserts(xml: bytes) -> tuple[list[str], list[str]]:
	root = objectify.fromstring(xml)
	# Asserts with flag "warning" or "information" do not make the invoice invalid.
	failed_asserts = root.xpath(
		"//svrl:failed-assert[not(@flag = 'warning' or @flag = 'information')]/svrl:text",
		namespaces={"svrl": "http://purl.oclc.org/dsdl/svrl"},
	)
	warnings = root.xpath(
		"//svrl:failed-assert[@flag = 'warning' or @flag = 'information']/svrl:text"
		" | //svrl:successful-report/svrl:text",
		namespaces={"svrl": "http://purl.oclc.org/dsdl/svrl"},
	)
	errors = [failed_assert.text.strip() for failed_assert in failed_asserts if failed_assert.text]
	warnings = [warning.text.strip() for warning in warnings if warning.text]
	return errors, warnings


def get_validation_report(xml_string: str, stylesheet_file: str) -> bytes:
	# DOCTYPE / DTD rejected by shared Saxon DISALLOW_DOCTYPE (see eu_einvoice.saxon).
	return transform_to_string(xml_string, stylesheet_file).encode("utf-8")
