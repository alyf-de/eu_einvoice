## European e-Invoice

Create and import european e-invoices with ERPNext

> [!WARNING]
> This app is under active development and should **not** yet be used in production environments. Things can **break and change at any time**.

## Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app eu_einvoice
```

## Usage

### Sales Invoice

To create a new eInvoice, open a **Sales Invoice** and click on "..." > "Download eInvoice".

For german government customers, the "Leitwegs-ID" should be entered into the field _Customer's Purchase Order_ of the **Sales Invoice**. This way it will show up in the XML's `BuyerReference` element.

The following fields of the **Sales Invoice** are currently considered for the eInvoice:

- Invoice type (credit note, corrected invoice, commercial invoice)
- Invoice number
- Invoice date
- Due date
- Language
- Currency
- Company Name
- Company Address
- Company Tax ID
- Company Phone (fetched from **Company**)
- Company Email (fetched from **Company**)
- Customer Name
- Customer Address
- Customer's Purchase Order (doubles as "Leitwegs-ID" for german government customers)
- Customer's Purchase Order Date
- Customer's Tax ID
- Items:
    - Item Name
    - Description
    - Company's Item Code
    - Customer's Item Code
    - Delivery Note number and date
    - Quantity + Unit
    - Rate
    - Net Amount
    - Amount
- Payment terms:
    - Description
    - Due date
    - Amount
    - Early Payment Discount
        - Percentage or Amount
        - Due date
- Tax Breakup / Taxes and Charges Calculation
- Total
- Discount Amount
- Net Total
- Total Taxes and Charges
- Grand Total
- Total Advance
- Outstanding Amount

### Purchase Invoice

To import a new eInvoice, create a new **E Invoice Import** and upload the XML file.

## Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/eu_einvoice
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.

### Dependencies

- [drafthorse](https://pypi.org/project/drafthorse/) by Raphael Michel, released under the Apache License 2.0
    Used to create and parse XML invoices
- [factur-x](https://pypi.org/project/factur-x/) by Alexis de Lattre, released unser a BSD License
    Used to extract XML data from a PDF file

## License

Copyright (C) 2024 ALYF GmbH

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or(at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with this program. If not, see <https://www.gnu.org/licenses/>.
