### European e-Invoice

Create and import european e-invoices with ERPNext

> [!WARNING]
> This app is under active development and should **not** yet be used in production environments. Things can **break and change at any time**.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app eu_einvoice
```

### Contributing

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

- [drafthorse](https://pypi.org/project/drafthorse/)
    Create and parse ZUGFeRD XML invoices
- [factur-x](https://pypi.org/project/factur-x/)
    Extract XML file from a PDF file

### Sales Invoice

For german government customers, the "Leitwegs-ID" should be entered into the field _Customer's Purchase Order_ of the **Sales Invoice**. This way it will show up in the XML's `BuyerReference` element.

### License

gpl-3.0
