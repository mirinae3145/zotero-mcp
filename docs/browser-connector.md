# Saving with the browser Connector

`zotero-cli browser save` invokes the paired Connector's toolbar-save action in an already open browser tab.
The browser runs its translators and attachment download code with its own session, cookies, proxy handling and access prompts.
Keep the publication open and complete any login or item-selection dialog in that browser.
The CLI does not copy browser credentials or independently download the publisher PDF.

## Setup

Use a build of the Connector fork that includes Browser CLI, and run the CLI from this checkout or install this fork's Python package.
Ordinary released Connectors do not implement this protocol.
To run this checkout without replacing your installed CLI, use `uv run --project /path/to/zotero-mcp --no-dev zotero-cli` in place of `zotero-cli` below.

1. Start Zotero desktop and select the library or collection to save into.
1. Load the development Connector in Chrome, Edge or Firefox and complete its first-use prompt.
1. Run `zotero-cli browser serve` in a separate terminal.
   It listens only on `127.0.0.1:23120` and creates `~/.config/zotero-mcp/browser-bridge.token` if absent.
1. Open Connector preferences → Advanced → Browser CLI.
   Enter the port and the contents of the token file, enable Browser CLI, and click Apply.
1. Open the publication in the paired browser and run the commands below.

```sh
zotero-cli --json browser status
zotero-cli --json browser tabs
zotero-cli --json browser targets
zotero-cli --json browser save --tab-id 42 --target L4
```

Use the tab ID and target ID returned by the commands; `42` and `L4` are examples.
`--target` is a guard: it requires that target to be selected in Zotero before starting the save.
It does not silently change Zotero's destination.
Omitting it uses the currently selected target, like clicking the toolbar button.
Connector `L`/`C` IDs are desktop tree IDs, not web API group IDs or item keys.
Group saves use the same selected-target path as manual saves; no personal-library search is used to infer success.

The broker must be reachable as the same localhost port from both the browser and CLI.
For Windows browsers with WSL, verify `browser status` first; if localhost forwarding is unavailable, run the broker and CLI on Windows.
Do not expose the broker on a LAN interface.
A second browser/profile uses a separate port and token file via `--port` and `--token-file` on each command.

## Results and retries

A save waits for the Connector's translator and attachment-saving operations.
By default it fails if a stored PDF was not confirmed for each returned item.
Use `--allow-no-pdf` when a metadata-only or webpage save is intentional.
Metadata can already have been created when PDF acquisition fails; failures retain the save receipt under `data` in JSON output.

Receipts include the save `sessionID`, selected target/library, translated item titles and attachment completion states.
`connectorItemID` and `connectorAttachmentID` are session-local IDs, **not Zotero item keys**.
A stored PDF receipt confirms the Connector's storage operation, not that the file is the complete publisher version.
Use the existing library tools to inspect the resulting item and read the correct full-text attachment, especially when supplementary PDFs are present.

`browser save` preserves toolbar behavior: repeating a save in the same page session reopens its result, while a reload or another tab may create another item.
Check the library for duplicates before a new save.

On timeout the CLI reports a job ID and leaves that job running:

```sh
zotero-cli --json browser result JOB_ID --timeout 180
```

Do not submit another save to recover a lost response.
The broker claims jobs once; a disconnected or restarted extension does not replay an uncertain click.
Results are retained in memory until the broker exits (up to 1000 jobs).
Inspect Zotero before restarting or retrying an unresolved save.
Disable Browser CLI in Connector preferences and stop the broker when finished.

## Development checks

The broker contract tests run without a user's Zotero library:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_browser_bridge.py -v
```

They exercise real loopback HTTP authentication, group-target request forwarding, single dispatch, result recovery and token-file creation.
Connector-side tests and the browser's cookie-bound PDF integration test live in the Connector fork.
