---
title: Custom logic
order: 40
roles:
  - System Manager
---

This app provides hooks to add custom logic to the eInvoice creation process:

- `before_einvoice_generation`

    Called right before the eInvoice is generated. The hook function receives the **Sales Invoice** as an argument and can modify it.

- `after_einvoice_generation`

    Called right after the eInvoice is generated. The hook function receives the **Sales Invoice** and the generated eInvoice as arguments.

For example, your `myapp/hooks.py` could look like this:

```python
doc_events = {
	"Sales Invoice": {
		"before_einvoice_generation": "myapp.einvoice.before_einvoice_generation",
		"after_einvoice_generation": "myapp.einvoice.after_einvoice_generation",
	}
}
```

And your `myapp/einvoice.py` like this:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from drafthorse.models.document import Document
    from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice


def before_einvoice_generation(doc: "SalesInvoice", event: str):
    """Modify the Sales Invoice object before generating the eInvoice."""
    doc.customer_name = "Special Customer Name"


def after_einvoice_generation(doc: "SalesInvoice", event: str, einvoice: "Document"):
    """Modify the generated eInvoice after it was created."""
    einvoice.trade.agreement.buyer.name = doc.customer_name
```

> [!WARNING]
> These methods are also triggered during the `validate` event of the **Sales Invoice**. In this case, if you change the Sales Invoice object, these changes will be saved to the database.
