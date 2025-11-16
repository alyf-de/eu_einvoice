# API Documentation

This document describes the programmatic APIs available in the EU E-Invoice app.

## Table of Contents

- [Audit Logging API](#audit-logging-api)
- [Alert System API](#alert-system-api)
- [Schematron Caching API](#schematron-caching-api)
- [Validation API](#validation-api)
- [Settings API](#settings-api)

## Audit Logging API

Module: `eu_einvoice.audit`

### `log_einvoice_event()`

Log an e-invoice event for auditing and debugging.

**Signature**:
```python
def log_einvoice_event(
    reference_doctype: str,
    reference_name: str,
    action: Literal["Generated", "Validated", "Imported", "Exported", "Failed"],
    profile: str | None = None,
    duration_ms: int | None = None,
    errors: str | None = None,
    warnings: str | None = None,
) -> None
```

**Parameters**:
- `reference_doctype`: DocType name (e.g., "Sales Invoice", "Purchase Invoice")
- `reference_name`: Document name (e.g., "INV-2024-00001")
- `action`: Type of operation performed
- `profile`: E-invoice profile used (optional)
- `duration_ms`: Operation duration in milliseconds (optional)
- `errors`: Error messages (optional)
- `warnings`: Warning messages (optional)

**Example**:
```python
from eu_einvoice.audit import log_einvoice_event

log_einvoice_event(
    reference_doctype="Sales Invoice",
    reference_name="INV-2024-00001",
    action="Validated",
    profile="EN 16931",
    duration_ms=250,
    errors="",
    warnings="Missing optional field: buyer reference"
)
```

**Notes**:
- Respects the "Enable Audit Log" setting
- Silently fails if logging fails (won't break main flow)
- Logs are stored in Error Log with method prefix "E-Invoice:"

### `get_audit_logs()`

Retrieve audit logs for e-invoice operations.

**Signature**:
```python
def get_audit_logs(
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    action: str | None = None,
    limit: int = 100,
) -> list[dict]
```

**Parameters**:
- `reference_doctype`: Filter by document type (optional)
- `reference_name`: Filter by document name (optional)
- `action`: Filter by action type (optional)
- `limit`: Maximum number of logs to return (default: 100)

**Returns**: List of dictionaries with log details

**Example**:
```python
from eu_einvoice.audit import get_audit_logs

# Get all logs for a specific invoice
logs = get_audit_logs(
    reference_doctype="Sales Invoice",
    reference_name="INV-2024-00001"
)

# Get last 50 validation logs
validation_logs = get_audit_logs(action="Validated", limit=50)

# Get all logs
all_logs = get_audit_logs(limit=1000)
```

**Return Format**:
```python
[
    {
        "name": "error-log-123",
        "creation": "2024-01-15 10:30:00",
        "method": "E-Invoice: Validated",
        "error": "Reference: Sales Invoice INV-2024-00001\n...",
    },
    ...
]
```

## Alert System API

Module: `eu_einvoice.alerts`

### `check_and_send_validation_failure_alerts()`

Check for validation failures and send alert emails if configured.

**Signature**:
```python
def check_and_send_validation_failure_alerts() -> None
```

**Example**:
```python
from eu_einvoice.alerts import check_and_send_validation_failure_alerts

# Run manually
check_and_send_validation_failure_alerts()
```

**Usage in Scheduler**:

Add to your app's `hooks.py`:
```python
scheduler_events = {
    "daily": [
        "eu_einvoice.alerts.check_and_send_validation_failure_alerts"
    ],
    # Or run more frequently:
    "hourly": [
        "eu_einvoice.alerts.check_and_send_validation_failure_alerts"
    ],
}
```

**Notes**:
- Only sends emails if "Alert on Validation Failure" is enabled
- Checks last 24 hours for failed invoices
- Sends HTML-formatted email with failure details
- Automatically skips if no failures found

### `send_validation_failure_alert()`

Send an email alert about validation failures.

**Signature**:
```python
def send_validation_failure_alert(
    email: str,
    failed_invoices: list[dict],
    period_hours: int = 24,
) -> None
```

**Parameters**:
- `email`: Recipient email address
- `failed_invoices`: List of failed invoice dictionaries
- `period_hours`: Time period covered by the alert (default: 24)

**Example**:
```python
from eu_einvoice.alerts import send_validation_failure_alert
import frappe

# Get failed invoices
failed_invoices = frappe.get_all(
    "Sales Invoice",
    filters={"einvoice_is_correct": 0},
    fields=["name", "customer", "posting_date", "grand_total", "einvoice_profile"]
)

# Send alert
send_validation_failure_alert(
    email="admin@example.com",
    failed_invoices=failed_invoices,
    period_hours=24
)
```

## Schematron Caching API

Module: `eu_einvoice.schematron`

### `get_validation_errors()`

Validate XML against Schematron rules with automatic caching.

**Signature**:
```python
def get_validation_errors(
    xml_string: str,
    einvoice_profile: EInvoiceProfile,
) -> tuple[list[str], list[str]]
```

**Parameters**:
- `xml_string`: XML invoice content as string
- `einvoice_profile`: Profile enum (EInvoiceProfile.EN16931, etc.)

**Returns**: Tuple of (errors, warnings)

**Example**:
```python
from eu_einvoice.schematron import get_validation_errors
from eu_einvoice.utils import EInvoiceProfile

xml_content = """<?xml version="1.0" encoding="UTF-8"?>..."""

errors, warnings = get_validation_errors(
    xml_string=xml_content,
    einvoice_profile=EInvoiceProfile.EN16931
)

if errors:
    print(f"Validation failed with {len(errors)} errors")
else:
    print("Validation successful!")
```

### `clear_cache()`

Clear the compiled stylesheet cache.

**Signature**:
```python
def clear_cache() -> None
```

**Example**:
```python
from eu_einvoice.schematron import clear_cache

# Clear cache (e.g., after updating validation rules)
clear_cache()
```

### `get_cache_info()`

Get information about the current cache state.

**Signature**:
```python
def get_cache_info() -> dict
```

**Returns**: Dictionary with cache statistics

**Example**:
```python
from eu_einvoice.schematron import get_cache_info

info = get_cache_info()
print(f"Cache size: {info['cache_size']} entries")
print(f"Caching enabled: {info['caching_enabled']}")
print(f"Cached stylesheets: {info['cached_stylesheets']}")
```

**Return Format**:
```python
{
    "cache_size": 2,
    "caching_enabled": True,
    "cached_stylesheets": [
        "/path/to/EN16931-CII-validation-preprocessed.xsl",
        "/path/to/XRechnung-CII-validation.xsl"
    ]
}
```

## Validation API

Module: `eu_einvoice.european_e_invoice.api`

### `test_validation()`

Test e-invoice validation for an uploaded file (Frappe whitelisted API).

**Signature**:
```python
@frappe.whitelist()
def test_validation(test_file: str, profile: str) -> dict
```

**Parameters**:
- `test_file`: File URL from Frappe file upload
- `profile`: Profile name as string ("BASIC", "EN 16931", "EXTENDED", "XRECHNUNG")

**Returns**: Dictionary with validation results

**Example (Python)**:
```python
import frappe

result = frappe.call(
    "eu_einvoice.european_e_invoice.api.test_validation",
    test_file="/files/test-invoice.xml",
    profile="EN 16931"
)

if result["success"]:
    print("Validation passed!")
else:
    print(f"Validation failed with {result['error_count']} errors")
    for error in result["errors"]:
        print(f"  - {error}")
```

**Example (JavaScript)**:
```javascript
frappe.call({
    method: "eu_einvoice.european_e_invoice.api.test_validation",
    args: {
        test_file: "/files/test-invoice.xml",
        profile: "EN 16931"
    },
    callback: (r) => {
        if (r.message.success) {
            frappe.msgprint("Validation passed!");
        } else {
            frappe.msgprint({
                title: "Validation Failed",
                message: r.message.errors.join("<br>"),
                indicator: "red"
            });
        }
    }
});
```

**Return Format**:
```python
{
    "success": True,
    "errors": [],
    "warnings": ["Optional field missing: ..."],
    "profile": "EN 16931",
    "error_count": 0,
    "warning_count": 1
}
```

### `download_sample_invoice()`

Download a sample e-invoice XML file (Frappe whitelisted API).

**Signature**:
```python
@frappe.whitelist()
def download_sample_invoice() -> None
```

**Example (JavaScript)**:
```javascript
frappe.call({
    method: "eu_einvoice.european_e_invoice.api.download_sample_invoice",
    callback: (r) => {
        // File will be downloaded automatically
    }
});
```

**Notes**:
- Returns EN 16931 compliant sample XML
- Automatically triggers browser download
- Filename: `sample-einvoice-en16931.xml`

## Settings API

Module: `eu_einvoice.european_e_invoice.doctype.e_invoice_settings.e_invoice_settings`

### `get_statistics()`

Get usage statistics for e-invoices (Frappe whitelisted method).

**Signature**:
```python
@frappe.whitelist()
def get_statistics(self) -> dict
```

**Example (Python)**:
```python
settings = frappe.get_single("E Invoice Settings")
stats = settings.get_statistics()

print(f"Total generated: {stats['total_generated']}")
print(f"Success rate: {stats['success_rate']}%")
```

**Example (JavaScript)**:
```javascript
frappe.call({
    method: "eu_einvoice.european_e_invoice.doctype.e_invoice_settings.e_invoice_settings.get_statistics",
    callback: (r) => {
        let stats = r.message;
        console.log(`Success rate: ${stats.success_rate}%`);
    }
});
```

**Return Format**:
```python
{
    "total_generated": 150,
    "validation_passed": 145,
    "validation_failed": 5,
    "total_imported": 30,
    "success_rate": 96.7,
    "profile_breakdown": [
        {"profile": "EN 16931", "count": 100},
        {"profile": "XRECHNUNG", "count": 50}
    ],
    "cache_info": {
        "cache_size": 2,
        "caching_enabled": True
    }
}
```

### `clear_validation_cache()`

Clear the Schematron validation cache (Frappe whitelisted method).

**Signature**:
```python
@frappe.whitelist()
def clear_validation_cache(self) -> dict
```

**Example (Python)**:
```python
settings = frappe.get_single("E Invoice Settings")
result = settings.clear_validation_cache()

print(result["message"])
print(f"Cache before: {result['cache_before']['cache_size']}")
print(f"Cache after: {result['cache_after']['cache_size']}")
```

**Example (JavaScript)**:
```javascript
frappe.call({
    method: "eu_einvoice.european_e_invoice.doctype.e_invoice_settings.e_invoice_settings.clear_validation_cache",
    callback: (r) => {
        frappe.show_alert({
            message: r.message.message,
            indicator: "green"
        });
    }
});
```

**Return Format**:
```python
{
    "message": "Cache cleared successfully",
    "cache_before": {"cache_size": 2, "caching_enabled": True},
    "cache_after": {"cache_size": 0, "caching_enabled": True}
}
```

## Common Use Cases

### 1. Custom Validation Hook

Add custom validation logic before e-invoice generation:

```python
# your_app/hooks.py
doc_events = {
    "Sales Invoice": {
        "before_einvoice_generation": "your_app.einvoice.validate_custom_fields",
    }
}

# your_app/einvoice.py
def validate_custom_fields(doc, event):
    """Custom validation before e-invoice generation."""
    if doc.einvoice_profile == "XRECHNUNG" and not doc.buyer_reference:
        frappe.throw("Buyer Reference is required for XRechnung")
```

### 2. Monitor Validation Failures

Create a custom report for validation failures:

```python
import frappe
from eu_einvoice.audit import get_audit_logs

def get_validation_failures_report(days=7):
    """Get report of validation failures."""
    from frappe.utils import add_to_date, now_datetime

    # Get audit logs for failures
    logs = get_audit_logs(action="Failed", limit=1000)

    # Filter by date range
    cutoff_date = add_to_date(now_datetime(), days=-days)
    recent_logs = [
        log for log in logs
        if log["creation"] >= cutoff_date
    ]

    return recent_logs
```

### 3. Batch Validate Invoices

Validate multiple invoices programmatically:

```python
import frappe
from eu_einvoice.schematron import get_validation_errors
from eu_einvoice.utils import EInvoiceProfile

def batch_validate_invoices(invoice_names):
    """Validate multiple invoices and return results."""
    results = []

    for name in invoice_names:
        doc = frappe.get_doc("Sales Invoice", name)

        # Generate XML (simplified - use actual generation logic)
        from eu_einvoice.european_e_invoice.doctype.sales_invoice.sales_invoice import generate_einvoice_xml
        xml_string = generate_einvoice_xml(doc)

        # Validate
        errors, warnings = get_validation_errors(
            xml_string,
            EInvoiceProfile(doc.einvoice_profile)
        )

        results.append({
            "invoice": name,
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        })

    return results
```

### 4. Custom Alert Logic

Send alerts based on custom criteria:

```python
from eu_einvoice.alerts import send_validation_failure_alert
import frappe

def send_custom_alert():
    """Send alert for high-value failed invoices."""
    failed_invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "einvoice_is_correct": 0,
            "grand_total": [">", 10000],  # High-value invoices only
            "docstatus": 1
        },
        fields=["name", "customer", "posting_date", "grand_total", "einvoice_profile"]
    )

    if failed_invoices:
        send_validation_failure_alert(
            email="manager@example.com",
            failed_invoices=failed_invoices,
            period_hours=24
        )
```

## Error Handling

All APIs follow these error handling patterns:

1. **Validation errors**: Raise `frappe.ValidationError` with user-friendly message
2. **Permission errors**: Respect Frappe's permission system
3. **Silent failures**: Audit logging fails silently to not break main flow
4. **Logging**: All errors logged via `frappe.log_error()`

**Example**:
```python
try:
    from eu_einvoice.audit import log_einvoice_event

    log_einvoice_event(
        reference_doctype="Sales Invoice",
        reference_name="INV-123",
        action="Generated"
    )
except Exception as e:
    # Audit logging failed, but don't break the main flow
    frappe.log_error(title="Audit Log Failed", message=str(e))
```

## Type Hints

The codebase uses type hints for better IDE support:

```python
from typing import Literal
from eu_einvoice.utils import EInvoiceProfile

# Literal types for actions
action: Literal["Generated", "Validated", "Imported", "Exported", "Failed"]

# Enum for profiles
profile: EInvoiceProfile = EInvoiceProfile.EN16931
```

## Further Reading

- [README.md](README.md) - Full user documentation
- [CHANGELOG.md](CHANGELOG.md) - Version history and migration guides
- [Frappe Framework Docs](https://frappeframework.com/docs) - Framework documentation
