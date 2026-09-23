# EU E-Invoice Copilot Instructions

This is a Frappe Framework app for creating and importing European e-invoices with ERPNext.

## Code Style & Formatting

### Python
- **Indentation**: Use tabs (width: 4 spaces) for Python files
- **Line length**: Max 110 characters (configured in ruff)
- **Target version**: Python 3.10+
- **Formatter**: Ruff (replaces Black)
- **Linter**: Ruff (replaces Flake8, isort, etc.)
- **Quote style**: Double quotes (`"`)
- **Line endings**: LF (Unix style)
- Always add final newline and trim trailing whitespace

### JavaScript/Vue
- **Indentation**: Use tabs (width: 4 spaces)
- **Line length**: Max 99 characters
- **Formatter**: Prettier for JS/Vue/SCSS files
- **Linter**: ESLint (extends `eslint:recommended`)
- **Line endings**: LF (Unix style)
- Always add final newline and trim trailing whitespace

### JSON
- **Indentation**: 2 spaces (not tabs)
- **No final newline** in JSON files

## Frappe Framework Rules

### Python API Best Practices
- ❌ `frappe.db.commit()` → ✅ Let framework handle transactions
- ❌ `print()` in DocTypes → ✅ `frappe.msgprint()` or `frappe.logger()`
- ❌ `map()`, `filter()` → ✅ List comprehensions
- ❌ `eval(expression)` → ✅ `frappe.safe_eval(expression)`
- ❌ `get_value("Single DocType", "Single DocType", field)` → ✅ `get_single_value("Single DocType", field)`
- ❌ Global DB calls → ✅ Wrap in functions (multitenancy)
- ❌ `frappe.cache().set/get()` → ✅ `frappe.cache().set_value/get_value()`

### DocType Controllers
- ❌ `def after_save():` → ✅ Use valid hooks: `after_insert`, `on_update`, `before_save`
- ❌ Modify without commit in post-save hooks: `self.status = "New"` → ✅ `self.db_set("status", "New")`
- ❌ Modify child tables while iterating → ✅ Iterate over copy of list

### Translations
- Always wrap user-facing strings in translation functions:
  - Python: `_("Text")` (underscore function)
  - JavaScript: `__("Text")` (double underscore function)
- ❌ `_("")` or `__("")` → ✅ Remove empty translations
- ❌ `_("{}")` or `_("{0}")` → ✅ Use variables directly
- ❌ `_("  text  ")` → ✅ `_("text")` (no trailing spaces)
- ❌ `_("Hello {}".format(name))` → ✅ `_("Hello {0}").format(name)`
- ❌ `_("Text") + _("More")` → ✅ `_("Text More")` (single translation)
- ❌ `_("Long " + "text")` → ✅ `_("Long text")` (no concatenation)

### JavaScript API Best Practices
- ❌ `cur_frm` → ✅ `frm` parameter
- ❌ `in_list(list, item)` → ✅ `list.includes(item)`
- ❌ `frappe.utils.debounce(fn, 300)()` → ✅ Create once: `const debounced = frappe.utils.debounce(fn, 300)`

### Query Builder
- ❌ `.orderby("field", "desc")` → ✅ `.orderby("field", order=frappe.qb.desc)`

## Frappe Bench Commands

### Bench Location

- **Local development**: Varies by installation
- **GitHub Actions CI**: `/home/runner/frappe-bench`
- **App location in CI**: `/home/runner/frappe-bench/apps/eu_einvoice`

### Development
```bash
# Start development server
bench start

# Open Python console with Frappe context
bench --site test_site console

# Execute Python code in Frappe context
bench --site test_site execute "frappe.clear_cache()"
bench --site test_site execute "module.function_name()"

# Clear cache
bench clear-cache
bench clear-website-cache
```

### Testing
```bash
# Run all tests for the app
bench --site test_site run-tests --app eu_einvoice

# Run tests for a specific module
bench --site test_site run-tests --module eu_einvoice.european_e_invoice.doctype.e_invoice_import.test_e_invoice_import

# Run specific test method
bench --site test_site run-tests --module eu_einvoice.european_e_invoice.doctype.e_invoice_import.test_e_invoice_import --test test_method_name
```

### Translation
```bash
# Generate POT file (translation template)
bench generate-pot-file --app eu_einvoice

# Update PO files from POT
bench update-po-files --app eu_einvoice

# Build translation files
bench compile-po-to-mo --app eu_einvoice
```

### Database
```bash
# Run migrations
bench --site test_site migrate

# Backup database
bench --site test_site backup

# Restore database
bench --site test_site restore /path/to/backup.sql.gz

# Database console
bench --site test_site mariadb
```

## Pre-commit Hooks

This repository uses pre-commit hooks that run automatically on `git commit`:
- **trailing-whitespace**: Removes trailing whitespace (except JSON, TXT, CSV, MD, SVG)
- **check-yaml**: Validates YAML syntax
- **check-merge-conflict**: Prevents committing merge conflict markers
- **check-ast**: Validates Python syntax
- **check-json**: Validates JSON syntax
- **check-toml**: Validates TOML syntax
- **debug-statements**: Prevents committing debug statements
- **ruff**: Lints and auto-fixes Python code
- **ruff-format**: Formats Python code
- **prettier**: Formats JS/Vue/SCSS files
- **eslint**: Lints JavaScript files
- **commitlint**: Validates commit messages (conventional commits)

### Commit Message Format
Follow [Conventional Commits](https://www.conventionalcommits.org/):
```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `build`

Examples:
- `feat: add e-invoice import functionality`
- `fix(import): handle missing tax categories`
- `docs: update installation instructions`

## Project Structure

```
eu_einvoice/
├── european_e_invoice/
│   ├── doctype/          # DocType definitions and controllers
│   ├── custom/           # Customizations to ERPNext DocTypes
│   └── workspace/        # Workspace definitions
├── public/
│   ├── css/              # Stylesheets
│   ├── js/               # Client-side JavaScript
│   └── img/              # Images
├── templates/            # Jinja templates
├── schematron/           # XSL validation files for e-invoices
├── locale/               # Translation files (PO/POT)
├── patches/              # Database migration patches
└── hooks.py              # App hooks and configuration
```

## Dependencies

### Python
- **factur-x**: ~3.1 - Factur-X/ZUGFeRD PDF generation
- **drafthorse**: ~2025.1.0 - CII XML generation
- **saxonche**: ~12.5.0 - Schematron validation
- **lxml**: >=4.9.3,<6.0.0 - XML processing

### Frappe/ERPNext
- Requires Frappe >= 16.0.0-dev, < 17.0.0
- Requires ERPNext >= 16.0.0-dev, < 17.0.0

## Code Quality Checks

### Ignored Rules
See `pyproject.toml` for intentionally ignored rules (e.g., line length, mixed tabs/spaces for Frappe compatibility)

## ESLint Configuration

- **Environment**: Browser + Node + ES2022
- **Extends**: `eslint:recommended`
- **Globals**: Frappe framework globals (`frappe`, `__`, `cur_frm`, etc.)
- Most style rules disabled (handled by Prettier)
- `no-console` set to warning only

## Documentation & Communication Style

### Formatting in PR Descriptions, Docs, and Comments

Follow these conventions when writing documentation, comments, and PR descriptions:

- **DocTypes**: Use bold for DocType names
  - Example: **Sales Invoice**, **E Invoice Import**, **Customer**
- **Field labels**: Use italics for field labels (user-facing names)
  - Example: *Company*, *Tax Category*, *Enable E-Invoicing*
- **Field names**: Use inline code for technical field names
  - Example: `company`, `tax_category`, `enable_e_invoicing`
- **Prefer labels over names**: When describing functionality to users, mention field labels rather than technical field names
  - ✅ "Set the *Tax Category* field"
  - ❌ "Set the `tax_category` field"
- **Values and report names**: Use quotes for values, report names, and other string literals
  - Example: Set status to "Completed", run "Sales Invoice Summary" report

## Development Workflow

1. Create feature branch from `develop`
2. Make changes following code style guidelines
3. Pre-commit hooks run automatically on commit
4. Fix any linting/formatting issues
5. Push and create pull request
6. Ensure CI passes on PR

## Additional Notes

- This app handles Factur-X, ZUGFeRD, and XRechnung formats
- Schematron validation files are in `schematron/` directory
- Custom fields are defined in `custom_fields.py`
- Installation logic is in `install.py`
- Jinja filters/functions in `jinja.py`

