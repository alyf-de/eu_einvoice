"""Desk bootinfo for European e-Invoice."""

from __future__ import annotations


def boot_session(bootinfo):
	"""Expose protocol parent DocTypes so Desk JS can gate attachment decoration."""
	from eu_einvoice.european_e_invoice.protocol import PROTOCOL_PARENT_DOCTYPES

	bootinfo.eu_einvoice = {
		"protocol_parent_doctypes": list(PROTOCOL_PARENT_DOCTYPES),
	}
