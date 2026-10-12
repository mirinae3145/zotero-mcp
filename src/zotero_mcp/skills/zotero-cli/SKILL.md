---
name: zotero-cli
description: Read and write a Zotero library from the shell with the `zotero-cli` command - search papers by keyword or meaning, read PDF full text and page ranges, get and set metadata, manage collections, tags, notes and annotations, add items by DOI/URL/ISBN or a paired browser Connector, and export bibliographies. Use whenever the user asks about their Zotero library, references, citations, papers they have saved, or their reading notes.
---

# Zotero from the shell

`zotero-cli` reaches a Zotero library directly. Prefer it over the Zotero MCP
server when you have shell access: the MCP server's tool schemas cost ~14k
tokens of context on every request whether or not you use them, while this
costs only what you actually run.

Check it is set up before the first real call:

```bash
zotero-cli config          # prints the resolved Zotero settings
```

`config` reports settings; it does not prove that Zotero or a browser bridge is reachable.
Use the relevant library read or `browser status` to check the path you will use.
Local mode needs Zotero desktop running with its local API enabled; web mode needs `ZOTERO_API_KEY` and `ZOTERO_LIBRARY_ID`.
Distinguish a setup failure from a completed library query.

## Always pass --json when you will read the output

Default output is markdown for humans. `--json` gives you one object per
invocation with a stable shape, so you never parse prose:

```json
{"ok": true, "command": "search", "schema": 1, "data": {...}}
{"ok": false, "command": "search", "schema": 1, "error": {"message": "...", "code": "..."}}
```

Both go to **stdout**; `[INFO]`/`[WARN]` diagnostics go to stderr. Check `ok`
before using `data`. Run `zotero-cli --json-schema` for the full contract.

## The core loop

Almost every task is: **find keys → act on keys**. Item keys are 8
characters and are the currency of every command.

```bash
# 1. find - use --detail keys_only to keep the result small while browsing
zotero-cli --json search "attention mechanisms" --limit 10 --detail keys_only

# 2. act - metadata, full text, or a page range
zotero-cli --json get metadata ABCD1234
zotero-cli --json get fulltext ABCD1234
zotero-cli --json read ABCD1234 --start-page 3 --end-page 8
```

Pipe keys straight into the next call:

```bash
zotero-cli --json search "diffusion models" --limit 5 --detail keys_only \
  | jq -r '.data.items[].key' \
  | while read -r key; do zotero-cli --json get metadata "$key"; done
```

## Choosing a search mode

| Mode | Use it for | Command |
|---|---|---|
| `items` (default) | a title, author or phrase you know | `search "Vaswani attention"` |
| `semantic` | a topic or idea, no exact wording | `search --mode semantic "why transformers scale"` |
| `tag` | items you filed under a tag | `search --mode tag "to-read,important"` |
| `advanced` | structured field conditions | `search --mode advanced --conditions '[...]'` |
| `citekey` | a BibTeX citation key | `search --mode citekey smith2020` |
| `notes` | text inside your notes, not item fields | `search --mode notes "research question"` |

`semantic` needs the search index built (`zotero-cli db status` to check,
`zotero-cli db update` to build). If it is empty, fall back to `items` and
say why rather than reporting no results.

Every search covers one library — the active one. When you don't know which
library holds something, or a search comes up empty and the item might live in
a group library, add `--all-libraries`:

```bash
zotero-cli search "Cladder-Micus" --all-libraries
```

Each result is then labelled `**Library:** <name>`. It needs the SQLite backend,
the default in local mode, and errors clearly if it is not in use, so try it
once and fall back to per-library searches if it is refused. Tag filters work
with it; `--collection` does not, because a collection lives inside one library.

## Reading efficiently

`get fulltext` on a book-length PDF returns a great deal of text. When you
need one section, use the outline to find it and read only those pages. To
locate a passage by its words, `--find` returns the matching pages with short
snippets instead of the pages themselves:

```bash
zotero-cli --json outline ABCD1234
zotero-cli --json read ABCD1234 --find "robustness check"
zotero-cli --json read ABCD1234 --start-page 42 --end-page 55
```

For "what does my library say about X", prefer `search --mode semantic`
followed by targeted `get metadata` over reading whole papers.

## Paging

Listings cap at `--limit`. When more exists, the response says so and names
the next offset:

```bash
zotero-cli --json get collection-items QS7TQPPA --limit 100 --offset 100
```

Keep going until `data.count` is less than `--limit`.

## Writing

Most library write commands report what they did as text under `data.text`.
Browser commands return structured job results; failed saves can retain partial results under `data`.

```bash
zotero-cli add doi 10.1038/s41586-021-03819-2 -c "Reading List"
zotero-cli edit ABCD1234 --title "Corrected Title" --add-tags reviewed
zotero-cli notes create --item-key ABCD1234 --text "Key finding: ..."
zotero-cli batch --item-keys A1B2C3D4,E5F6G7H8 --add-tags screened
```

Note text is Markdown, converted to Zotero's note format: headings, lists,
tables, code, links (`zotero://` ones stay clickable), `$math$` and `$$display$$`,
plus `<u>`, `<s>`, `<sub>`, `<sup>`, `<mark>` and `<span style="color: red">` or
`background-color` (red, orange, yellow, green, purple, magenta, blue, gray, or
`#hex`). Pipe long text with `--text -`. HTML starting with `<p>`/`<div>` is
kept to the note editor's tags, so a note read with `notes list --raw-html`,
edited and written back with `notes update` keeps its citations and images;
`notes update --append` adds to the end.

Before a destructive change (`delete`, `duplicates merge`, a `batch` over
many items), confirm with the user and show what will be affected. `delete
item` refuses notes unless `--allow-note` is passed.

## Registration and PDF acquisition paths

Choose a path according to the requested workflow, available access and applicable user instructions.
The skill does not prescribe a universal acquisition priority.

| Path | Commands | Behavior |
| --- | --- | --- |
| Identifier or page registration | `add doi`, `add url`, `add isbn` | Metadata lookup; DOI/URL PDF discovery uses HTTP and available open-access sources, without the browser's login session |
| Browser Connector | `browser save` | Runs the paired browser's toolbar-save path, including its translators, cookies and attachment handling |
| Local file or existing item | `add file`, `attach` | Import a file or add an attachment to an existing item; a linked URL is not a stored PDF |

Check the intended library for an existing DOI/ISBN and title before creating an item.
`add` reuses existing items by default; `--if-exists skip` leaves them unchanged.
Repeated DOI registration does not repair a missing PDF.
Browser saves preserve toolbar behavior and are not generally idempotent across reloads or different tabs.
Reuse existing items and inspect their attachments rather than retrying registration as a repair strategy.

### Browser workflow

Check `zotero-cli browser --help` before relying on this feature: the CLI and Connector must both support this fork's bridge.
A running `browser serve` process and a paired, enabled Connector are prerequisites.
The broker and browser must share the loopback address; preserve the configured `--port` and `--token-file` on every related command.
Pairing happens in the Connector's Browser CLI preferences using the broker's token file; do not print the token in reports.
A missing command or connection is a setup blocker, not evidence that a paper is unavailable.

| Command | Purpose |
| --- | --- |
| `browser serve` | Run the local broker; reuse an existing broker rather than starting competing instances |
| `browser status` | Check `data.connected` before dispatching browser work |
| `browser tabs`, `browser targets` | Get actual tab IDs and the selected Zotero target |
| `browser open URL` | Open a publication without saving |
| `browser focus --tab-id ID` | Explicitly show a retained tab when user interaction is needed |
| `browser inspect --tab-id ID` | Inspect title, detection state and translators of an existing tab |
| `browser save --url URL` | Open a publication tab and invoke the toolbar save |
| `browser save --tab-id ID` | Save or resume an already open tab |
| `browser result JOB_ID` | Retrieve the outcome of the original job without repeating its save |

`--url` opens a tab in the running paired browser; it does not launch the browser process.
For unobtrusive acquisition in a running browser, use `save --url URL --background --close-on-success` when appropriate to the user's workflow.
Both forks must support these options; older Connectors may ignore them.
Only a successful save's own newly created background tab is eligible for automatic cleanup.
Check `tab.closed` and `tab.closeReason`; failure, uncertainty or user interaction preserves the tab.
Existing tabs and resumed `--tab-id` saves are never automatically closed.
After a failure, inspect the retained tab and use `browser focus --tab-id ID` when user action is needed; do not open another copy of the URL.
The caller decides when to notify or focus, while the Connector only executes the requested action.
CAPTCHA extensions are optional environment components, not a dependency or guarantee of automatic challenge completion.
Storage success and tab cleanup do not replace correct-paper/PDF verification.

Use the browser profile appropriate for the required access; a new test profile does not inherit another profile's login session.
`--target` checks the selected destination and does not select or move it.
Its `L`/`C` identifiers are Connector desktop tree IDs, not web API library/group IDs or item keys.
A shared destination or bridge blocker should be resolved once before continuing a batch.

```bash
zotero-cli --json browser status
zotero-cli --json browser targets
zotero-cli --json browser save --url https://doi.org/10.1038/s41586-021-03819-2 --target L4
zotero-cli --json browser inspect --tab-id 42
zotero-cli --json browser result JOB_ID --timeout 180
```

Use observed IDs; `L4` and `42` are examples.
A `loading` or `needs_user_action` result identifies a tab to inspect, not a reason to open the same URL repeatedly.
Allow the current load to settle, or wait for required login/human verification, then resume that tab with `--tab-id`.
Challenge-title detection is a heuristic; it does not establish the cause of a block or guarantee CAPTCHA handling.
The tool does not solve CAPTCHAs.

### Batch and deferred user action

For multiple new publications, first check duplicates in the intended library, then use `browser batch URL1 URL2 ... --target TARGET_ID`.
The automatic pass uses background tabs, conservative close-on-success and a two-second gap (`--interval` can adjust it).
Review `data.completed`, `deferred`, `failed`, `unattempted` and `stopped`; these batch lists replace the single-job `data.result` shape.
Handle retained attention tabs together after the automatic pass, rather than interrupting the user for each paper as it arrives.
Do not close successful tabs that were retained because the user touched them.
Inspect partial saves before retrying; a timeout/unknown outcome stops new submissions and preserves its job ID.
While a job is still running, the serialized broker cannot execute a queued `focus`/`inspect`; ask the user to visit the tab directly and recover with `result`.
Unexpected prompts inside a translator/download can still stop a batch; preflight deferral is not cancellation of a running save.

CLI library selection and the Connector's desktop selection are independent.
Before duplicate checks and after saving, use `library list` / `library switch` to query the intended library, and `browser targets` to verify the save destination.
Do not pass a Connector desktop `libraryID` as a web group ID; resolve the group ID from the library listing.

### Verify the resulting work

For browser jobs, inspect `data.result` as well as top-level `ok`.
By default a save needs a confirmed stored PDF for each returned item; `--allow-no-pdf` explicitly permits metadata-only saves.
A failure may still have created metadata or an attachment.
A timeout does not cancel the original job: recover it with `browser result`, and inspect the library before considering another write.
Observing an already-running job is not a new save attempt.
Broker results last only for that process's lifetime, so do not restart it to clear an unresolved save.

`sessionID`, `connectorItemID` and `connectorAttachmentID` are save-session identifiers, not Zotero item keys.
Find the actual saved item in the intended library and compare its DOI/title with the requested paper; a successful translator can still identify the wrong work.
Check child attachments and read the specific full-text attachment, since parent-key reading may select supplementary material.
Report metadata creation, stored file acquisition, correct-paper verification and full-text/version verification separately.
A candidate URL, a linked attachment or a save receipt alone does not prove that the complete requested paper was obtained.

## Reading and annotating a paper

```bash
zotero-cli get children ITEM_KEY                          # the PDF's attachment key
zotero-cli read ITEM_KEY --start-page 1 --end-page 99     # end page clamps to the last page
zotero-cli path ATTACHMENT_KEY                            # the PDF file on disk
zotero-cli open ITEM_KEY --page 7                         # show that page in the Zotero reader
zotero-cli open --annotation ANNOTATION_KEY               # jump to one annotation and select it
```

When the user is reading along in Zotero and asks where something is, point
the reader at it with `open` rather than only quoting a page number.
`annotations create --open` does both in one step.

Extracted text is reliable for prose and unreliable for math, figures and
tables: symbols drop out and table cells run together. `read` flags each
page where that happens ("Garbled in this text: Equation (1), Table 2").
For those pages, look at the page itself if you can view images:

```bash
zotero-cli read ITEM_KEY --start-page 4 --format image                         # PNG per page, up to 10
zotero-cli read ITEM_KEY --start-page 4 --format image --rect 0.35,0.49,0.3,0.05   # zoom into one region
```

To annotate, plan everything, check it, then write it in one run:

1. `zotero-cli --json layout ATTACHMENT_KEY` lists figure, table and
   equation boxes with their captions and a paste-ready `rect_arg`.
2. Write one JSON object per line: `{"page": 4, "text": "exact words",
   "comment": "...", "color": "yellow"}` for a highlight, or
   `{"page": 3, "rect": "x,y,w,h", "comment": "..."}` for a box, or
   `{"page": 1, "note": "x,y", "comment": "..."}` for a sticky note
   centered on a point (its text is the comment). Copy
   highlight text exactly from `read`; it is searched for on that page and
   two pages either side.
3. `zotero-cli annotations batch --attachment-key ATTACHMENT_KEY --file plan.jsonl --dry-run`
   shows the words each highlight would cover. Fix every miss.
4. Run it again without `--dry-run`. Anything that did not land is listed
   under `data.results` with `ok: false`, and the exit code is 1.

Colors take Zotero's names: yellow, red, green, blue, purple, magenta,
orange, gray. Three or four colors with fixed meanings read better than
eight; say what they mean in a note on the item.

Writes in local mode need a one-time `zotero-mcp authorize-local` (Zotero 10
or newer) or web API credentials. A write refused for that reason says so.

## When something looks wrong

- Empty search results: check `zotero-cli config` and, for semantic mode,
  `zotero-cli db status`. Do not report "you have no papers on X" until you
  know the library is actually reachable and indexed.
- `ok: false`: read `error.message`. It names the cause.
- A partial-results note on a search means the scan was cut short, not that
  nothing else matched. Narrow the query and re-run.
- An item marked `"deleted": true` is in the trash. Do not treat it as part
  of the live library.

## Bound recovery and report failures together

For routine acquisition, make one normal attempt on the chosen path.
Retry once only when a specific observed condition has changed, such as completed login, a cleared challenge, corrected destination, or a finished load.
If appropriate, try one distinct, documented alternative source or path; if it still fails, stop that item and continue independent work.
Changing URL variants, browser settings or request parameters without new evidence is not a fresh recovery strategy.
An explicitly requested diagnosis or development test may go further, with a concrete hypothesis and recorded outcomes; do not turn an ordinary import into open-ended debugging.

When a shared bridge, authentication or destination prerequisite fails, stop the affected batch rather than reproducing that failure for every paper.
If an operation may already have written data, verify the existing job/item before any new save.
Respect a user's instruction to stop investigating an item or route.

Keep a compact failure list while working and give one consolidated report with the successful results.
For each unresolved item include its title/DOI, attempted route, observed failure stage, any created item/job/tab IDs and the action or condition needed to continue.
Group common blockers once; distinguish not attempted, metadata-only, file acquired but unverified, wrong-work results, and verified acquisition.
Report backups or restored files as preservation, not as new downloads.
Use canonical source links and omit tokens, cookies and signed URL parameters.

## Full command reference

`reference.md` in this skill directory lists every command and flag. Read it
when you need something not covered above. `zotero-cli <command> --help`
also works for any single command.
