## European e-Invoice

Create and import e-invoices with ERPNext.

This app converts between ERPNext and UN/CEFACT Cross-Industry-Invoice (CII) XML in the BASIC, EN 16931, EXTENDED, and XRECHNUNG profiles. All profiles except "XRECHNUNG" can be embedded in a PDF file (ZUGFeRD / Factur-X). UBL invoices and Peppol transport are out of scope.

<a href="https://www.zugferd-community.net/de/alyf_gmbh" target="_blank">
    <img src="img/member_partner_quer_klein.jpg" alt="We are a ZUGFeRD Community Member Partner" width="250"/>
</a >

---

## Documentation

User documentation lives under [`eu_einvoice/docs/`](eu_einvoice/docs). [Compendium](https://github.com/alyf-de/compendium) serves these files in Desk at `/app/docs` when that app is installed.

You can also read the Markdown files in this repository:

- [Overview](eu_einvoice/docs/en/eu_einvoice/index.md)
- [Setup](eu_einvoice/docs/en/eu_einvoice/setup.md)
- [Sales Invoice](eu_einvoice/docs/en/eu_einvoice/sales-invoice.md)
- [Purchase Invoice](eu_einvoice/docs/en/eu_einvoice/purchase-invoice.md)
- [Custom logic](eu_einvoice/docs/en/eu_einvoice/custom-logic.md)

## Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/alyf-de/eu_einvoice --branch $MAJOR_VERSION
bench install-app eu_einvoice
```

Please use a branch (`MAJOR_VERSION`) that matches the major version of ERPNext you are using. For example, `version-14` or `version-15`. If you are a developer contributing new features, you'll want to use the `develop` branch instead.

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

    Used to create and parse XML invoices.

- [factur-x](https://pypi.org/project/factur-x/) by Alexis de Lattre, released under a BSD License

    Used to extract XML data from PDF files, and to create PDF files with embedded XML.

- [SaxonC](https://pypi.org/project/saxonche/) by Saxonica

    Used for XSL transformation (validate XML against schematron).

- [lxml](https://github.com/lxml/lxml) by Infrae

    Used for general XML parsing.

- [SchXslt](https://github.com/schxslt/schxslt) by David Maus

    Used to convert Schematron files to XSL.

## Sponsors

Many thanks to the following companies for sponsoring the initial development of this app:

- aepfel+birnen IT GmbH
- axessio Hausverwaltung GmbH
- Burkhard Baumsteigtechnik GmbH & Co. KG
- DriveCon GmbH
- ibb testing gmbh
- itsdave GmbH
- iXGate UG
- Kautenburger IT GmbH
- MERECS Engineering GmbH
- Royal Software GmbH
- voidsy GmbH
- … and many more

> [!NOTE]
> We only list companies that have explicitly agreed to have their name published here. If you want to be listed here too, please send us a short note by email.

## License

Copyright (C) 2024 ALYF GmbH

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or(at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with this program. If not, see <https://www.gnu.org/licenses/>.
