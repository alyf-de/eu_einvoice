import json
import subprocess
import sys
from unittest import TestCase

from frappe.tests import UnitTestCase

from eu_einvoice import saxon as saxon_runtime
from eu_einvoice.saxon import UnsafeXMLError
from eu_einvoice.schematron import extract_failed_asserts, get_validation_errors
from eu_einvoice.utils import EInvoiceProfile

# Deliberately incomplete, so that every profile reports failed asserts.
XML = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
	xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
	xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
	xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
	<rsm:ExchangedDocumentContext>
		<ram:GuidelineSpecifiedDocumentContextParameter>
			<ram:ID>urn:cen.eu:en16931:2017</ram:ID>
		</ram:GuidelineSpecifiedDocumentContextParameter>
	</rsm:ExchangedDocumentContext>
	<rsm:ExchangedDocument>
		<ram:ID>TEST-1</ram:ID>
		<ram:TypeCode>380</ram:TypeCode>
	</rsm:ExchangedDocument>
</rsm:CrossIndustryInvoice>
"""

SVRL_REPORT = b"""<?xml version="1.0" encoding="UTF-8"?>
<svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl">
	<svrl:failed-assert id="BR-01" flag="fatal" location="/">
		<svrl:text>Fatal assert</svrl:text>
	</svrl:failed-assert>
	<svrl:failed-assert id="BR-02" location="/">
		<svrl:text>Assert without flag</svrl:text>
	</svrl:failed-assert>
	<svrl:failed-assert id="BR-TMP-7" flag="warning" location="/">
		<svrl:text>Warning assert</svrl:text>
	</svrl:failed-assert>
	<svrl:failed-assert id="BR-03" flag="information" location="/">
		<svrl:text>Information assert</svrl:text>
	</svrl:failed-assert>
	<svrl:successful-report id="BR-04" location="/">
		<svrl:text>Report</svrl:text>
	</svrl:successful-report>
</svrl:schematron-output>
"""


class TestSchematron(TestCase):
	def test_processor_and_stylesheet_are_reused(self):
		first = get_validation_errors(XML, EInvoiceProfile.EN16931)
		processor = saxon_runtime._processor
		second = get_validation_errors(XML, EInvoiceProfile.EN16931)

		self.assertTrue(first[0])
		self.assertEqual(first, second)
		self.assertIs(saxon_runtime._processor, processor)
		stylesheets = [path for path, _mtime, _cwd, _params in saxon_runtime._executables]
		self.assertEqual(len(stylesheets), len(set(stylesheets)))

	def test_concurrent_validation(self):
		# Runs in a fresh process: the crash only happens when Saxon is first used from
		# several threads at once (like in a gthread worker), and it kills the whole process.
		result = subprocess.run(
			[sys.executable, "-c", CONCURRENT_SCRIPT, XML],
			capture_output=True,
			text=True,
			timeout=600,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

		results = json.loads(result.stdout)
		self.assertEqual(len(results), 40)
		for profile, errors, warnings in results:
			expected = get_validation_errors(XML, EInvoiceProfile[profile])
			self.assertEqual((errors, warnings), expected)

	def test_visualization_cwd_does_not_break_facturx_codedb(self):
		"""Viewer set_cwd must not stick; Factur-X stylesheets load *_codedb.xml relatively."""
		from pathlib import Path

		from eu_einvoice.schematron import SCHEMATRON_DIR
		from eu_einvoice.visualization import VISUALIZATION_DIR, render_einvoice_html

		fixture = (
			Path(__file__).resolve().parents[1]
			/ "visualization"
			/ "test_instances"
			/ "included-notes-bg-1-uncefact.xml"
		)
		render_einvoice_html(fixture.read_text(), lang="en")
		self.assertEqual(saxon_runtime._xslt_cwd, str(VISUALIZATION_DIR.resolve()))

		# BASIC uses document('FACTUR-X_BASIC_codedb.xml'); wrong cwd would fail the transform.
		errors, _warnings = get_validation_errors(XML, EInvoiceProfile.BASIC)
		self.assertTrue(errors)
		self.assertEqual(saxon_runtime._xslt_cwd, str(SCHEMATRON_DIR.resolve()))


CONCURRENT_SCRIPT = """
import json, sys, threading
from eu_einvoice.schematron import PROFILE_TO_XSL, get_validation_errors

xml, profiles, results = sys.argv[1], list(PROFILE_TO_XSL), []

def validate(offset):
	for i in range(10):
		profile = profiles[(offset + i) % len(profiles)]
		errors, warnings = get_validation_errors(xml, profile)
		results.append((profile.name, errors, warnings))

threads = [threading.Thread(target=validate, args=(offset,)) for offset in range(4)]
for thread in threads:
	thread.start()
for thread in threads:
	thread.join()
print(json.dumps(results))
"""


class TestExtractFailedAsserts(UnitTestCase):
	def test_warning_flags_are_not_errors(self):
		errors, warnings = extract_failed_asserts(SVRL_REPORT)

		self.assertEqual(errors, ["Fatal assert", "Assert without flag"])
		self.assertEqual(warnings, ["Warning assert", "Information assert", "Report"])


class TestGetValidationErrors(UnitTestCase):
	def test_doctype_is_rejected(self):
		with self.assertRaises(UnsafeXMLError):
			get_validation_errors("<!DOCTYPE invoice><invoice/>", EInvoiceProfile.EN16931)
