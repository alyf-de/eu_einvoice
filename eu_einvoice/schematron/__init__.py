import os
import threading
from pathlib import Path

from lxml import objectify
from saxonche import PySaxonProcessor

from eu_einvoice.utils import EInvoiceProfile

PROFILE_TO_XSL = {
	EInvoiceProfile.BASIC: "Factur-X_1.07.2_BASIC.xsl",
	EInvoiceProfile.EN16931: "EN16931-CII-validation-preprocessed.xsl",
	EInvoiceProfile.EXTENDED: "Factur-X_1.07.2_EXTENDED.xsl",
	EInvoiceProfile.XRECHNUNG: "XRechnung-CII-validation.xsl",
}

# saxonche is not safe to use with one processor per call from several threads: the
# processor counter is shared by the class and garbage-collecting a processor while
# another thread still works with Saxon releases the shared GraalVM environment, which
# kills the whole process (Fatal StackOverflowError). Compiling the stylesheet on every
# call also costs seconds and a lot of memory. So the process keeps a single processor
# (created lazily, never released) and the compiled stylesheets, and all Saxon calls are
# serialized by a lock.
_lock = threading.RLock()
_processor = None
_xslt30_processor = None
_executables = {}  # {(stylesheet_file, mtime_ns): XsltExecutable}


def _reset_lock_in_child():
	# A fork while another thread holds the lock would leave the child with a lock nobody releases.
	global _lock
	_lock = threading.RLock()


if hasattr(os, "register_at_fork"):
	os.register_at_fork(after_in_child=_reset_lock_in_child)


def get_validation_errors(xml_string: str, profile: EInvoiceProfile):
	return get_errors_from_stylesheet(xml_string, PROFILE_TO_XSL[profile])


def get_errors_from_stylesheet(xml_string: str, stylesheet: str):
	stylesheet_path = Path(__file__).parent / stylesheet
	report = get_validation_report(xml_string, str(stylesheet_path))
	return extract_failed_asserts(report)


def extract_failed_asserts(xml: bytes) -> tuple[list[str], list[str]]:
	root = objectify.fromstring(xml)
	failed_asserts = root.xpath(
		"//svrl:failed-assert/svrl:text",
		namespaces={"svrl": "http://purl.oclc.org/dsdl/svrl"},
	)
	warnings = root.xpath(
		"//svrl:successful-report/svrl:text",
		namespaces={"svrl": "http://purl.oclc.org/dsdl/svrl"},
	)
	errors = [failed_assert.text.strip() for failed_assert in failed_asserts if failed_assert.text]
	warnings = [warning.text.strip() for warning in warnings if warning.text]
	return errors, warnings


def get_validation_report(xml_string: str, stylesheet_file: str) -> bytes:
	global _processor, _xslt30_processor
	with _lock:
		if _xslt30_processor is None:
			if _processor is None:
				_processor = PySaxonProcessor(license=False)
			_xslt30_processor = _processor.new_xslt30_processor()

		# Accessing this property attaches the current thread to the Saxon environment.
		_processor.attach_current_thread
		key = (stylesheet_file, os.stat(stylesheet_file).st_mtime_ns)
		input_node = _processor.parse_xml(xml_text=xml_string)
		try:
			executable = _executables.get(key)
			if executable is None:
				for outdated in [k for k in _executables if k[0] == stylesheet_file]:
					del _executables[outdated]
				executable = _xslt30_processor.compile_stylesheet(stylesheet_file=stylesheet_file)
				_executables[key] = executable

			report = executable.transform_to_string(xdm_node=input_node)
			return report.encode("utf-8")
		except Exception:
			_executables.pop(key, None)
			_xslt30_processor = None
			raise
