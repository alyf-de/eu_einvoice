---
title: European e-Invoice
order: 0
roles:
  - Accounts User
  - Accounts Manager
  - System Manager
---

Create and import e-invoices with ERPNext.

This app reads and writes electronic invoices according to the UN/CEFACT Cross-Industry-Invoice (CII) standard in these profiles:

- BASIC
- EN 16931
- EXTENDED
- XRECHNUNG

All profiles except "XRECHNUNG" can be embedded in a PDF file, known as ZUGFeRD or Factur-X.

This app cannot read or write UBL invoices. It also does not provide a special way of sending or receiving e-invoices (for example Peppol). It converts between ERPNext's internal data model and the XML format of the standards above.

## Guides

- [Setup](/app/docs/en/eu_einvoice/setup)
- [Sales Invoice](/app/docs/en/eu_einvoice/sales-invoice)
- [Purchase Invoice](/app/docs/en/eu_einvoice/purchase-invoice)
- [Custom logic](/app/docs/en/eu_einvoice/custom-logic)
