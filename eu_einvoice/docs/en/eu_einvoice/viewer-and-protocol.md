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

On **E Invoice Import** and **Purchase Invoice**, attachments (sidebar) and *Attach* / *Attach Image* fields whose **File** has an **E Invoice Protocol** show a small file icon next to the file name (`file` while Unsupported, green `file-check` when Valid, red `file-x` when Invalid). Clicking it opens the same viewer for that file. When a protocol exists, the viewer dialog offers **Open Protocol** to jump to that **E Invoice Protocol**.

The viewer uses the [KoSIT XRechnung visualization](https://github.com/itplr-kosit/xrechnung-visualization) stylesheets (vendored in the app) and the same SaxonC engine as Schematron validation. It supports:

- CII XML (including XML extracted from ZUGFeRD / Factur-X PDF)
- UBL Invoice / CreditNote XML (viewer only; import and validation remain CII-only)

The HTML preview is for human readability. It is not a legal archive format; keep the original XML (or PDF with embedded XML).

The preview runs in a sandboxed iframe (`allow-scripts` and `allow-downloads` only - not `allow-same-origin`) so invoice HTML cannot access Desk. A Content-Security-Policy meta tag further restricts network and script sources inside the iframe.

## Protocol

**E Invoice Protocol** stores an audit trail for e-invoice files (1:1 with **File**). Deleting the linked **File** deletes the protocol. Reference documents (**E Invoice Import**, **Purchase Invoice**) may still be deleted without deleting the protocol while the **File** remains (`ignore_links_on_delete`).

There is no settings table for protocol: attach-time Schematron runs only when a **File** is attached to **E Invoice Import** or **Purchase Invoice** (hard-coded). Each **File** gets its own protocol row; results are not copied between files.

### Permissions

**Accounts User** and **Accounts Manager** may read **E Invoice Protocol**; **System Manager** has full access. *Company* (`company`) is copied from the reference document when that DocType has a *Company* field (e.g. **Purchase Invoice**, **E Invoice Import**) so User Permissions on **Company** scope the Desk list.

### When validation runs

1. **Attach (validation):** When a **File** is attached to **E Invoice Import** or **Purchase Invoice**, Schematron is queued (short queue) via `get_validation_errors`. This covers **File** saves (sidebar attach) and parent document saves that set an *Attach* / *Attach Image* field on the parent or in a child table (e.g. **Purchase Invoice** *Supplier Invoice File*): selecting an existing File Manager upload attaches the owned orphan without File events, so validation also runs from the parent `on_update` path.
2. **E Invoice Import:** Schematron runs in `before_save` when *E Invoice* (`einvoice`) changes, but the protocol row is written only after Frappe attaches the **File** (`on_update`), so the result keeps `file` and `content_hash`. Extra sidebar files on the same import are validated like other attachments. XRechnung imports also run EN 16931, matching outbound and attach-time validation. Missing or unrecognized CII guidelines use the EN 16931 ruleset.
3. **Existing protocol:** Once a protocol row exists for a **File**, Schematron is not re-run on re-attach; the protocol's reference fields are updated if needed.

Each protocol stores validator, version, profile, *Ruleset Hash* (`ruleset_hash`), error/warning counts, error list, and result ("Valid" / "Invalid" / "Unsupported"). Non-CII formats (e.g. UBL) are marked "Unsupported" because Schematron validation is CII-only for now. XML with a DOCTYPE is stored as "Invalid" with that reason (Saxon refuses DTD / entity expansion); it is not left without a protocol row.

Protocol File hooks never raise into the upload/save path: unexpected failures are logged so a bad XML file cannot block **File** insert.
