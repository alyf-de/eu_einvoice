"""Process-wide SaxonC runtime: one processor, serialized access, cached stylesheets.

saxonche is not safe to use with one processor per call from several threads: the
processor counter is shared by the class and garbage-collecting a processor while
another thread still works with Saxon releases the shared GraalVM environment, which
kills the whole process (Fatal StackOverflowError). Compiling the stylesheet on every
call also costs seconds and a lot of memory. So the process keeps a single processor
(created lazily, never released) and the compiled stylesheets, and all Saxon calls are
serialized by a lock.
"""

from __future__ import annotations

import os
import threading
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from saxonche import PySaxonApiError, PySaxonProcessor

# Invoices do not need a DOCTYPE. With this parser feature, Saxon does not process DTDs or entities.
DISALLOW_DOCTYPE = (
	"http://saxon.sf.net/feature/parserFeature?uri=http%3A//apache.org/xml/features/disallow-doctype-decl"
)

_lock = threading.RLock()
_processor = None
_xslt30_processor = None
# Last cwd applied to the shared XSLT processor (must be reset every call).
_xslt_cwd: str | None = None
# {(stylesheet_file, mtime_ns, cwd, params_tuple): XsltExecutable}
_executables: dict = {}


class UnsafeXMLError(Exception):
	"""XML contains a DOCTYPE / DTD that must not be processed."""


def _reset_lock_in_child():
	# A fork while another thread holds the lock would leave the child with a lock nobody releases.
	global _lock
	_lock = threading.RLock()


if hasattr(os, "register_at_fork"):
	os.register_at_fork(after_in_child=_reset_lock_in_child)


def _get_processor():
	"""Return the process-wide PySaxonProcessor. Caller must hold ``_lock``."""
	global _processor
	if _processor is None:
		_processor = PySaxonProcessor(license=False)
		_processor.set_configuration_property(DISALLOW_DOCTYPE, "true")
	# Accessing this property attaches the current thread to the Saxon environment.
	_processor.attach_current_thread
	return _processor


def get_saxon_version() -> str:
	"""Return the installed saxonche package version (no processor create/release)."""
	try:
		return f"saxonche {version('saxonche')}"
	except PackageNotFoundError:
		return "SaxonC unknown"


def transform_to_string(
	xml_text: str,
	stylesheet_file: str,
	*,
	cwd: str | None = None,
	parameters: dict[str, str] | None = None,
) -> str:
	"""Parse ``xml_text`` and transform with ``stylesheet_file`` under the shared lock.

	``cwd`` defaults to the stylesheet directory so relative ``document()`` loads
	(e.g. Factur-X ``*_codedb.xml``) resolve correctly. It is applied on every call
	so a previous visualization ``set_cwd`` cannot leak into Schematron.
	"""
	global _xslt30_processor, _xslt_cwd

	stylesheet_file = str(Path(stylesheet_file).resolve())
	# Always an absolute directory - never leave the previous call's cwd in place.
	cwd_value = str(Path(cwd).resolve()) if cwd else str(Path(stylesheet_file).parent)
	params_tuple = tuple(sorted((parameters or {}).items()))

	with _lock:
		proc = _get_processor()
		if _xslt30_processor is None:
			_xslt30_processor = proc.new_xslt30_processor()
			_xslt_cwd = None

		if _xslt_cwd != cwd_value:
			_xslt30_processor.set_cwd(cwd_value)
			_xslt_cwd = cwd_value

		key = (stylesheet_file, os.stat(stylesheet_file).st_mtime_ns, cwd_value, params_tuple)
		try:
			input_node = proc.parse_xml(xml_text=xml_text)
			executable = _executables.get(key)
			if executable is None:
				for outdated in [k for k in _executables if k[0] == stylesheet_file]:
					del _executables[outdated]
				if hasattr(_xslt30_processor, "clear_parameters"):
					_xslt30_processor.clear_parameters()
				for name, value in params_tuple:
					_xslt30_processor.set_parameter(name, proc.make_string_value(value))
				executable = _xslt30_processor.compile_stylesheet(stylesheet_file=stylesheet_file)
				_executables[key] = executable

			result = executable.transform_to_string(xdm_node=input_node)
			if result is None:
				raise RuntimeError(f"XSLT transform with {stylesheet_file} returned no result")
			return result
		except PySaxonApiError as exc:
			if "DOCTYPE" in str(exc).upper():
				raise UnsafeXMLError("XML with a DOCTYPE declaration is not allowed") from exc
			_executables.pop(key, None)
			_xslt30_processor = None
			_xslt_cwd = None
			raise
		except Exception:
			_executables.pop(key, None)
			_xslt30_processor = None
			_xslt_cwd = None
			raise
