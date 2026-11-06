---
title: Viewer and Protocol
order: 35
roles:
  - Accounts User
  - Accounts Manager
  - System Manager
---

## E-invoice viewer

Use **View e-Invoice** on **Sales Invoice** (menu) or **E Invoice Import** (button) to open a standardized HTML preview.

On any form, attachments (sidebar) and *Attach* / *Attach Image* fields whose **File** has an **E Invoice Protocol** show a small file icon next to the file name (`file` while Pending or Unsupported, green `file-check` when Valid, red `file-x` when Invalid). Clicking it opens the same viewer for that file. When a protocol exists, the viewer dialog offers **Open Protocol** to jump to that **E Invoice Protocol**.

The viewer uses the [KoSIT XRechnung visualization](https://github.com/itplr-kosit/xrechnung-visualization) stylesheets (vendored in the app) and the same SaxonC engine as Schematron validation. It supports:

- CII XML (including XML extracted from ZUGFeRD / Factur-X PDF)
- UBL Invoice / CreditNote XML (viewer only; import & validation remains CII-only)

The HTML preview is for human readability. It is not a legal archive format; keep the original XML (or PDF with embedded XML).

The preview runs in a sandboxed iframe (`allow-scripts` and `allow-downloads` only — not `allow-same-origin`) so invoice HTML cannot access Desk. Dangerous URL schemes from invoice fields are stripped server-side before display.

## Protocol

**E Invoice Protocol** stores an audit trail for e-invoice files (1:1 with **File**). Deleting the linked **File** deletes the protocol. Reference documents (**E Invoice Import**, **Purchase Invoice**, …) may still be deleted without deleting the protocol while the **File** remains (`ignore_links_on_delete`). An original protocol that other rows still reference via *Copied From* (`copied_from`) cannot be deleted until those copies are removed (File trash clears those links first).

Read access for Accounts roles follows the linked **File** or an existing, readable reference document (either is enough), the same way attachment visibility works: unattached private uploads stay with the file owner, attached files follow the parent document’s permissions, and public files remain readable. The Desk list uses the same rule and requires the reference document to still exist. *Company* (`company`) is copied from the reference document when that DocType has a *Company* field (e.g. **Purchase Invoice**, **Sales Invoice**, **E Invoice Import**) for list filters and User Permissions; upload stubs without a reference keep *Company* empty until the **File** is attached, but those rows are still scoped by **File** access rather than left company-wide. **System Manager** can read all protocol rows.

### Settings

On **E Invoice Settings** → *Protocol*:

| Field | Behaviour |
|-------|-----------|
| *Create Protocol on Upload* (`create_protocol_on_upload`) | `"None"` (default), `"Only selected DocTypes"`, or `"All files"` |
| *Protocol DocTypes* (`protocol_doctypes`) | DocTypes that receive upload stubs (in *Only selected* mode) and Schematron validation on attach. Defaults: **E Invoice Import**, **Purchase Invoice**. |

### Two phases

1. **Upload (optional):** When the setting is not `"None"`, each uploaded e-invoice **File** gets its own protocol row (1:1 with **File**) with detection time, file name, *Content Hash* (`content_hash`, the **File** blob MD5), *XML Hash* (`file_hash`, SHA-256 of the XML payload), and detected format. PDF detection is queued so large PDFs do not block the upload request. If another protocol already has a settled result for the same `content_hash`, `file_hash`, **and** *Ruleset Hash* (`ruleset_hash`, SHA-256 of the bundled Schematron stylesheet(s) for the profile), the new row copies that result and sets *Copied From* (`copied_from`) to the original protocol — Schematron does not run again. After a ruleset update, the same XML is validated again; older protocol rows stay as history. Non-CII formats (e.g. UBL) are marked **"Unsupported"** (grey) because Schematron validation is CII-only for now. On a Pending stub that still has a linked **File**, use **Validate** to queue Schematron manually (same background job as attach-time validation).
2. **Attach (validation):** When the **File** is attached to a listed DocType and its protocol is still Pending, Schematron is queued (short queue) via `get_validation_errors` unless a matching original exists (same content, XML, and ruleset hashes) - then the result is copied as above. Uploads that are already attached enqueue that same validate job from `after_insert` (not a separate stub job), so stub creation and Schematron share one worker. This covers **File** saves (sidebar attach) and parent document saves that set an *Attach* / *Attach Image* field on the parent or in a child table (e.g. **Purchase Invoice** *Supplier Invoice File*): selecting an existing File Manager upload attaches the owned orphan without File events, so validation also runs from the parent `on_update` path. For **E Invoice Import**, Schematron runs in `before_save` when *E Invoice* (`einvoice`) changes, but the protocol row is written only after Frappe attaches the **File** (`on_update`), so the result keeps `file` and `content_hash`. Extra sidebar files on the same import are validated like other attachments. XRechnung imports also run EN 16931, matching outbound and attach-time validation. Missing or unrecognized CII guidelines use the EN 16931 ruleset for both validation and hash reuse. Each protocol stores validator, version, profile, ruleset hash, error/warning counts, error list, and result ("Valid" / "Invalid" / "Unsupported").
3. **Propagate to siblings:** When a protocol becomes Valid, Invalid, or Unsupported, any other Pending protocol with the same `content_hash` and `file_hash` receives a copy of the result (with *Copied From* and `ruleset_hash` set). That covers File Manager uploads that stay Pending until the same blob is attached and validated as a new **File** row.

Protocol File hooks never raise into the upload/save path: detection and validation failures are logged and skipped so a bad XML file cannot block **File** insert.
