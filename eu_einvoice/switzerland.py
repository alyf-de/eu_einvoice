import re


def normalize_swiss_vat_id(vat_id: str) -> str:
	return vat_id.strip().replace(" ", "").replace("-", "").replace(".", "")[:12].upper()


def is_valid_swiss_vat_id(vat_id: str) -> bool:
	"""
	Validates a Swiss VAT ID (UID).
	Expected formats: 'CHE-123.456.789 MWST', 'CHE123456789', etc.
	"""
	# 1. Clean the string: keep only 'CHE' and the 9 digits
	# Removes dots, hyphens, spaces, and suffixes (MWST/TVA/IVA)
	normalized = normalize_swiss_vat_id(vat_id)

	# Check basic structure: Must start with CHE followed by exactly 9 digits
	if not re.match(r"^CHE\d{9}$", normalized):
		return False

	# 2. Extract the digits
	digits_str = normalized[3:]  # Skip 'CHE'
	digits = [int(d) for d in digits_str]

	# 3. Modulo 11 Checksum Calculation
	# Weights defined by the Swiss Federal Statistical Office (BFS)
	weights = [5, 4, 3, 2, 7, 6, 5, 4]

	# Calculate sum of (digit * weight) for the first 8 digits
	checksum_sum = sum(d * w for d, w in zip(digits[:8], weights, strict=True))

	# Calculate the remainder
	remainder = checksum_sum % 11

	# Determine the expected check digit
	# If remainder is 0, check digit is 0. Otherwise, 11 - remainder.
	expected_check_digit = (11 - remainder) % 11

	# 4. Final Validation
	# Important: In the Swiss system, if the expected digit is 10,
	# the UID is considered invalid (it is not assigned).
	actual_check_digit = digits[8]

	return expected_check_digit == actual_check_digit and expected_check_digit != 10
