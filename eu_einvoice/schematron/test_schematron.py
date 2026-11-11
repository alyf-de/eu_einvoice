from frappe.tests.utils import FrappeTestCase

from eu_einvoice.schematron import extract_failed_asserts

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


class TestExtractFailedAsserts(FrappeTestCase):
	def test_warning_flags_are_not_errors(self):
		errors, warnings = extract_failed_asserts(SVRL_REPORT)

		self.assertEqual(errors, ["Fatal assert", "Assert without flag"])
		self.assertEqual(warnings, ["Warning assert", "Information assert", "Report"])
