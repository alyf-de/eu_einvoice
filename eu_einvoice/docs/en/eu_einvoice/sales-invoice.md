---
title: Sales Invoice
order: 20
roles:
  - Accounts User
  - Accounts Manager
  - System Manager
---

To create an outgoing eInvoice, create a **Sales Invoice** and select the _E Invoice Profile_ you want to use.

## Fields used in the eInvoice

The following fields of the **Sales Invoice** are currently considered for the eInvoice:

- Invoice type (credit note, corrected invoice, commercial invoice)
- Invoice number
- Invoice date
- Due date
- From date
- To date
- Language
- Currency
- Company
    - Phone No
    - Email
    - Fax
    - Electronic Address Scheme
    - Electronic Address
- Company Name
- Company Address
    - Address Line 1
    - Address Line 2
    - Postcode
    - City
    - Country
- Company Contact Person
    - Full Name
    - Email Address (takes precedence over Company > Email)
    - Phone (takes precedence over Company > Phone No)
    - Department
- Company Tax ID
- Customer
    - Electronic Address Scheme
    - Electronic Address
- Customer Name
- Buyer Reference (fetched from **Sales Order** or **Customer**)
- Customer Address
    - Address Line 1
    - Address Line 2
    - Postcode
    - City
    - Country
    - Email ID (only if Contact Email is not set)
- Contact Email
- Contact Person
    - Full Name
    - Phone (takes precedence over Mobile No)
    - Email Address
    - Mobile No
    - Department
- Shipping Address
    - Address Title (falls back to _Customer Name_)
    - Address Line 1
    - Address Line 2
    - Postcode
    - City
    - Country
- Customer's Purchase Order
- Customer's Purchase Order Date
- Customer's Tax ID
- Items:
    - Item Name
    - Description
    - Company's Item Code
    - Customer's Item Code
    - Delivery Note number and date
    - Sales Order number and date (added on document level, only if there is exactly one Sales Order)
    - Quantity + Unit
    - Rate
    - Net Amount
    - Amount
- Terms and Conditions Details (converted to markdown)
- Incoterm and named place
- Payment Schedule
    - Mode of Payment -> Account -> Bank Account
        - IBAN
        - Bank
            - SWIFT Number
    - Description
    - Due date
    - Amount
    - Discount Type (must be "Percentage")
    - Discount
    - Discount Date
- Sales Taxes and Charges
    - The _Charge Type_ "Actual" is used as logistics or service charges. It is only supported by the eInvoice profiles "EXTENDED" and "XRECHNUNG". If you want to add VAT for the service charge, add a _Charge Type_ "On Previous Row Amount" or "On Previous Row Total" immediately after the service charge.
    - For _Charge Type_ "On Net Total", use a single tax line. This is currently the only reliable way to get a correctly calculated taxable amount. Invoices with mixed tax rates tend to produce rounding errors. This also happens if one of the tax lines has a 0-amount [1].
    - The _Charge Type_ "On Item Quantity" is not supported.
- Total
- Net Total
- Total Taxes and Charges
- Grand Total
- Total Advance
- Outstanding Amount
- Embedded Document
    This attachment field can be used to embed an additional supporting document into the e-invoice (XML-)file. For example, a time report in PDF format.

[1] If there is more than one tax line, the app approximates the taxable amount as `tax_amount / rate * 100`. It uses the rate from the tax row or from the corresponding Account. The correct taxable amount is only available starting from ERPNext v16. For earlier versions this approximation has a small error margin.

The actual delivery date is set to the latest posting date of the linked **Delivery Notes**, if available. Otherwise, it is set to the invoice's _To Date_ or _Posting Date_ (in that order of priority).

ERPNext will not accept negative quantities, and the e-invoice rules (BR-27) will not accept negative prices. To work around this, the app flips the signs: a line that would have had a negative price and positive quantity is instead sent with a positive price and a negative quantity.

Document-level discounts are currently not supported, because the e invoice standard requires much more information than just the discount amount (for example the reason and applicable VAT rate).

During validation of the **Sales Invoice**, the potential eInvoice is created and validated against the schematron rules for the selected _E Invoice Profile_, so that you can see any potential problems before submitting it.

## Export Sales Invoice as XML (XRechnung) or PDF+XML (ZUGFeRD)

To download the XML file (XRechnung), open a **Sales Invoice** and click on "..." > "Download eInvoice".

When you open the print preview of the **Sales Invoice** and click on "PDF", the generated PDF file will have the e-invoice XML embedded. An exception is the _E Invoice Profile_ "XRECHNUNG", which is intended to be a plain XML file. In this case, the PDF will not have the XML embedded.

> [!TIP]
> You can test both XML and PDF+XML files by re-importing them, using the **E Invoice Import** DocType.

## PDF/A-3 conversion

The app will automatically attempt to convert the PDF to PDF/A-3 format before embedding the XML. This ensures maximum compatibility with document management systems and long-term archival requirements.

This conversion is done using [Ghostscript](https://www.ghostscript.com/), a free, open-source interpreter for the PostScript language and for PDF files.

The conversion requires:

1. Ghostscript to be installed globally on your system/server
2. The ICC profile `srgb.icc` to be available in Ghostscript's search paths

If Ghostscript is installed and the conversion fails, the app will fall back to embedding the XML in a regular PDF file and log an error message.

## Embedding the Factur-X logo

If you like, you can embed one of the official Factur-X logos in your invoice PDF. This way, a human can easily identify the invoice as a Factur-X eInvoice.

To do this, use the `get_einvoice_logo` method in your jinja **Print Format**. This method returns a base64-encoded data URL, which can be used in an `<img>` tag.

```jinja
<img src="{{ get_einvoice_logo(doc.einvoice_profile) }}" alt="{{ doc.einvoice_profile }} e-invoice logo" />
```

The following logos are available:

BASIC | EN 16931 | EXTENDED
--- | --- | ---
![BASIC](assets/sales-invoice/fx-basic.png) | ![EN 16931](assets/sales-invoice/fx-en16931.png) | ![EXTENDED](assets/sales-invoice/fx-extended.png)

## External validation

You can upload an XML invoice file to https://www.itb.ec.europa.eu/invoice/upload and validate it as "CII Invoice CML". Use the _E Invoice Profile_ "EN 16931" for generating your invoice.

E-invoices according to the "XRECHNUNG" profile can be validated at https://erechnungsvalidator.service-bw.de.
