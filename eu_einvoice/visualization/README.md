# XRechnung Visualization (vendored)

HTML visualization stylesheets from
[itplr-kosit/xrechnung-visualization](https://github.com/itplr-kosit/xrechnung-visualization)
(Apache License 2.0).

**Pinned release:** `v2026-10-13` (compatible with XRechnung 3.0)

## Included files

Runtime assets for CII / UBL → intermediate XR XML → HTML (PDF/FOP path not vendored):

- `cii-xr.xsl`, `ubl-invoice-xr.xsl`, `ubl-creditnote-xr.xsl`
- `common-xr.xsl`, `functions.xsl`
- `xrechnung-html.xsl`, `xrechnung-viewer.css`, `xrechnung-viewer.js`, `FileSaver-v2.0.5.js`
- `l10n/de.xml`, `l10n/en.xml`

App-owned (not from KoSIT): `frappe-viewer-override.css` is injected after render to restyle the tab bar toward Frappe Desk form tabs.

## Refresh

1. Check out the desired tag from the KoSIT repo (or GitHub mirror).
2. Copy the files listed above from `src/xsl/` into this directory (keep `l10n/`).
3. Update the pinned release note in this README.
4. Smoke-test with `eu_einvoice.visualization.render_einvoice_html` on a fixture under `test_instances/`.
