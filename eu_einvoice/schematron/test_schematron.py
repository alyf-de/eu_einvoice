import json
import subprocess
import sys
from unittest import TestCase

from eu_einvoice import schematron
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


class TestSchematron(TestCase):
	def test_processor_and_stylesheet_are_reused(self):
		first = schematron.get_validation_errors(XML, EInvoiceProfile.EN16931)
		processor = schematron._processor
		second = schematron.get_validation_errors(XML, EInvoiceProfile.EN16931)

		self.assertTrue(first[0])
		self.assertEqual(first, second)
		self.assertIs(schematron._processor, processor)
		stylesheets = [path for path, _mtime in schematron._executables]
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
			expected = schematron.get_validation_errors(XML, EInvoiceProfile[profile])
			self.assertEqual((errors, warnings), expected)


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
