---
title: Self-billing (Purchase Invoice)
order: 35
roles:
  - Accounts User
  - Accounts Manager
  - System Manager
sources:
  - eu_einvoice/european_e_invoice/custom/purchase_invoice.py
  - eu_einvoice/european_e_invoice/custom/purchase_invoice.js
  - eu_einvoice/custom_fields.py
source_rev: cabc64e
---

In self-billing (German: _Gutschriftverfahren_), you issue the invoice for a supply you received, on behalf of your supplier. Typical cases are commissions for sales partners or credit notes for producers. The supplier must have agreed to this beforehand.

The app creates the e-invoice for such a "Gutschrift" from a **Purchase Invoice**. In this e-invoice, the **supplier is the seller** and **your company is the buyer**.

## Setup

1. **Company**: enter your VAT ID (_USt-IdNr._) in _Tax ID_.
2. **Supplier**: for every supplier you bill yourself:
    - Set _Self-billing E Invoice Profile_, for example "XRECHNUNG".
    - Enter the supplier's VAT ID or tax number (_Steuernummer_) in _Tax ID_. It is mandatory on every Gutschrift.
    - Add a **Contact** with name, phone and email, and tick _Is Primary Contact_, so that ERPNext fills it into new invoices. "XRECHNUNG" requires a seller contact.
    - Add a **Bank Account** for the supplier (_Party Type_ "Supplier") with the IBAN and tick _Is Default_. This is the account you pay to. Do not use the supplier's _Default Company Bank Account_: that is your own account.
3. **Code lists** (see [Setup](/app/docs/eu_einvoice/setup)):
    - Map the payment means (for example "58" SEPA Credit Transfer) to your **Mode of Payment** or **Payment Terms Template**.
    - For small businesses (German § 19 UStG), map the VAT category "E" and a matching VAT exemption reason code to a **Tax Category**. Without a mapping, the default exemption reason "vatex-eu-ae" (reverse charge) is used.
    - For reverse charge ("AE") or intra-community supplies ("K"), map the category to the **Tax Category** or the **Purchase Taxes and Charges Template**.

## Create a self-billed invoice

1. Create a **Purchase Invoice** and select the supplier. The _E Invoice Profile_ (tab "E Invoicing") is set from the supplier's _Self-billing E Invoice Profile_.
2. Add items and taxes as usual, then save.
3. When you save, the app creates the e-invoice and validates it. Problems are shown at the top of the form and in the "E Invoicing" tab.
4. Submit the invoice.
5. Download the XML file via "..." > "Download eInvoice". For profiles other than "XRECHNUNG", the PDF from the print preview contains the XML.

Only invoices with an _E Invoice Profile_ get an e-invoice. Ordinary purchase invoices stay unaffected.

Good to know:

- The profile is only a default in the form. You can change or clear it per invoice. Duplicates, amendments and returns keep the profile of the original.
- If **E Invoice Settings** > _Auto-attach XML File_ is enabled, invoices with "XRECHNUNG" get the XML attached on submit.
- **E Invoice Settings** > _Validate Invoice on Save/Submit_ and the error actions apply to self-billed invoices too.

## Reading validation messages

Messages about the **seller** refer to your **supplier**, messages about the **buyer** to your company. For example:

- `[BR-S-02]` or `[BR-DE-16]`: the supplier has no _Tax ID_.
- `[BR-DE-2]`: the invoice has no supplier contact, or the contact has no phone or email.
- `[BR-DE-23-a]`: the payment means is a bank transfer, but the supplier has no default bank account with an IBAN.

When you save, the app also warns about invoices that are probably not meant to be self-billed:

- The invoice is linked to the supplier's own e-invoice (**E Invoice Import**), so the supplier issued their own invoice.
- The supplier is an internal supplier.

It also warns when the supplier has no default bank account, or when the invoice has deducted taxes.

## What the e-invoice contains

- **Invoice type**: "389" (self-billed invoice). This code marks the e-invoice as a Gutschrift, so no extra note is needed.
- **Returns** (Create > Return / Debit Note): also "389", with negative amounts and a reference to the original invoice.
- **Amendments**: "384" (corrected invoice) with a reference to the cancelled invoice.
- **Invoice number**: the name of the **Purchase Invoice**, or the field set in **E Invoice Settings** > _Invoice Number Field_, if the **Purchase Invoice** has that field.
- **Buyer reference**: the **Purchase Order**, if all items come from exactly one. Otherwise the invoice number.
- **Seller**: supplier name, address, contact, tax ID and electronic address (or the contact's email).
- **Buyer**: your company with the _Billing Address_. The _Shipping Address_ is the delivery address.
- **Payment**: the supplier's default bank account (IBAN, BIC, account name) and the payment schedule.
- **Taxes**:
    - Rows with _Consider Tax or Charge for_ "Valuation" are left out. They only affect your stock value.
    - With reverse charge ("AE") or intra-community supply ("K"), the e-invoice states one 0 % VAT breakdown. Your own reverse charge rows, which add up to zero, are left out.
    - Deducted taxes (_Add or Deduct_ "Deduct", for example withholding tax) are not supported. The e-invoice will not be valid.

## Limits

- Deducted taxes are not supported.
- The **PDF on Submit** app embeds the XML only for **Sales Invoices**.
