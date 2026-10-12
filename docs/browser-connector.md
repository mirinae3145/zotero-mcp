# Saving with the browser Connector

`zotero-cli browser save` invokes the paired Connector's toolbar-save action in a browser tab.
Pass `--url` to open the publication automatically, or `--tab-id` to resume an existing tab.
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
zotero-cli --json browser save --url https://doi.org/10.1063/1.4982698 --target L4
zotero-cli --json browser inspect --tab-id 42
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

## Login and publisher checks

The paired browser must already be running; `--url` opens the tab for you but does not launch the browser process.
`browser open URL` opens a page without saving, and `browser inspect --tab-id ID` reports its current title, detection state and available translators.
The Connector waits up to 30 seconds for page detection.
Common challenge-page titles produce `state: needs_user_action`; a loading timeout produces `state: loading`.
These are observations based on page state, not a diagnosis of subscription rights or the challenge provider.
No item is created in these pre-save states.

Complete any login or human-verification prompt in the paired browser, then retry `browser save --tab-id ID` on that same tab.
The tool does not solve CAPTCHAs and does not import another browser profile's login session.
A publisher can also challenge a PDF request after metadata is created; those results may contain a partial save receipt and require inspecting the Connector's visible prompt.
Do not treat a fresh profile's failure as proof that your ordinary authenticated browser cannot download the paper.

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

## Background tabs and responsibility boundaries

`browser open URL --background` and `browser save --url URL --background` create an inactive tab without focusing its window.
Without the flag, URL commands retain foreground behavior.
This uses a running browser with its existing session; it is not headless execution and does not launch a browser process.

For an unattended save, add `--close-on-success` to a background URL save.
Only a tab created by that very request can be closed, after a confirmed successful receipt.
Existing tabs (including a tab retained from an earlier open or failed save) are never automatically closed.
Failures, uncertain outcomes and tabs activated, pinned or navigated elsewhere during saving are retained.
Results expose `tab.createdByRequest`, and when cleanup is requested, `tab.closed` plus a `tab.closeReason` when retained.
A cleanup failure does not invalidate a successful save; do not repeat the save to retry cleanup.
Closing a tab follows storage confirmation, not independent verification of the paper's identity or completeness.

Use `browser focus --tab-id ID` explicitly when a retained tab needs user interaction, then resume saving with `--tab-id ID`.
The Connector does not automatically focus failed tabs, solve CAPTCHAs, or control third-party challenge extensions.
Such extensions are optional browser-environment components; page readiness determines whether saving proceeds.

The Connector owns tab execution and conservative cleanup; the CLI/broker owns transport, job IDs and receipt recovery.
The caller owns duplicate checks, paper verification, retry limits and deciding when to notify the user or focus a tab.
A future headless launcher would own browser processes and profiles separately.

```sh
zotero-cli --json browser save --url https://doi.org/10.1002/pola.11083 --target L4 --background --close-on-success
zotero-cli --json browser focus --tab-id 42
zotero-cli --json browser save --tab-id 42 --target L4
```

Use the actual target and tab IDs returned in your environment.
Update both forks together to use these options; older Connectors may ignore unsupported request fields.

## Batch acquisition

Run `browser batch URL1 URL2 ... --target TARGET_ID` for new publications after checking the intended library for duplicates.
The caller must omit existing items with missing PDFs when its policy is to report them; the batch command does not query the library or repair existing items.
The command saves sequentially in background tabs, with a default two-second interval between papers (`--interval`, 0.5–60 seconds).
Confirmed saves close eligible request-created tabs using the same conservative cleanup rules as `save`.
It never focuses tabs during the automatic pass.

Completed challenge/loading results and detected multiple-item selectors are collected in `deferred`; terminal partial-save failures are collected in `failed`.
After the pass, review the combined report and handle retained tabs together, using `focus` only when interaction is needed.
Do not resubmit a partial save before checking its items and attachments.
User-touched successful tabs remain open; the caller must not close them to force a completely empty success-tab list.

The JSON envelope's `data` contains `completed`, `deferred`, `failed`, `unattempted` and `stopped`.
Any incomplete batch returns `ok: false` and exit code 1 while preserving all outcomes.
A timeout or unknown save outcome stops new submissions and retains the original job ID for `browser result`.
A shared setup or destination failure also stops the pass rather than repeating it for every paper.
An unexpected prompt inside an already-running translator/download may therefore stop a batch: this is not cancellation or a guarantee of fully unattended execution.
The broker serializes commands, so a still-running save also queues `focus`/`inspect`; ask the user to visit that tab directly while recovering the original result.

Do not combine a second independent batch with an unresolved first batch on the same broker.
Upgrade both forks together for batch preflight deferral.
