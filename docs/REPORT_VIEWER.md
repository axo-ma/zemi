# Interactive Dataset Report and terminal chat

Each Dataset Report has a neighbouring `<module>.dataset.cmd` launcher.
Double-click it to open the report in a local PyQt5 QWebEngine window. Markdown
is converted to HTML on demand using markdown-it-py, with HTML tables and
disclosures preserved. The viewer requires PyQt5, PyQtWebEngine,
markdown-it-py and beautifulsoup4 in the interpreter generating the reports.
There is no HTTP viewer server, Windows protocol registration or file association.

Dataset items have a one-based number in dataset order, shared across samples,
Dataset Reports, Item Reports and run tables/details. Numbers are local to the
module dataset; the original Item ID remains the identity. Changing dataset order
changes these display numbers. The narrow first `#` column links to the Item Report.
Its viewer width is 38 pixels. Workbook links remain in Item ID.
The dataset table uses its content width instead of stretching to fill the window.
Target, Matches and Sample columns stay adjacent, including during partial runs
with only a few samples. Additional samples use horizontal scrolling.

Dataset Report keeps #, Item ID and Target fixed at the left while Matches and all
Sample columns scroll horizontally. Other report tables keep their first column
fixed. Upper table headers remain fixed during vertical scrolling.
Existing Markdown files and checkmarks are unchanged. The viewer overlays chat
links on the existing prediction text or ✅. Links are blue and underlined on
hover; errors are red. The disclosure triangle expands immediately; clicking
its answer text opens the terminal instead. Missing/ambiguous contexts have no
chat link. Sample headings open Sample Reports; Matches opens Item Reports;
Markdown links render in the same window, and workbook links open through the
Windows default application. Back, Forward and Refresh support navigation.
The viewer uses the Dataset Report document-and-grid icon in the report palette
for its title bar, Alt+Tab and the Windows taskbar, with a dedicated Windows app ID.

## Captured context

Non-streaming calls through `assistant.clients.openai.client.chat.completions.create`
are automatically captured during a notebook run. The captured MIME output contains
the original messages, JSON request settings, original assistant answer and the
Arsenal configuration path/checksum and selected endpoint/model names. It contains
no transport credentials. These outputs are collected separately from predictions
and persisted in `report.json` and `<module>.dataset.chat.json`. The manifest is
indexed by dataset row and sample column. It does not send ground truth to the model.

The context is not reconstructed from shortened report text or regenerated encodings.
Changing the Arsenal configuration invalidates continuation until the original
file is restored. Credential references resolve through the existing InputStore.
Multiple calls/results, streaming calls, direct non-Arsenal clients, tools requiring
execution and unsupported request fields are not silently approximated. Old reports
without capture remain readable but cannot offer exact continuation.

## Terminal lifecycle

The terminal uses the existing Arsenal OpenAI adapter, not llama-cli. The same
model/artifact, runtime build, thread settings, reasoning mode and generation
request parameters are retained. A managed model starts in Model Mode on its own
loopback port, with context size `max(original, 32768)` tokens. This deliberately
changes the context budget and routing mode, not the saved experiment. Model
support and RAM/VRAM can limit startup. No smaller context or truncated history
is substituted silently. External endpoints retain their provider context limit;
the client cannot enlarge a remote model's context.

The terminal appends user/assistant messages after the captured original answer.
Failed requests leave the preceding conversation intact. Paste a multiline request
directly: the pasted block stays in one message. Enter submits the message;
Alt+Enter inserts a line break while typing. `/exit`, EOF or Ctrl+C closes the chat.
Terminal input uses prompt_toolkit from the project interpreter. Conversations
are stored separately under the run's `chats/` directory after successful turns.
No original prediction, evaluation or report result is changed. On Windows the
chat-owned model process is attached to a kill-on-close Job Object, so closing
the terminal also releases that model. External servers are never terminated.

`/help` lists the terminal commands. `/settings` shows every supported request
override and its source; absent values are reported as `not set` with an unknown
effective value instead of assuming an inference-server default. `/set` supports
`reasoning`, `temperature`, `top_p`, `top_k`, `min_p`, `max_tokens`, `seed`,
`repeat_penalty`, `presence_penalty`, `frequency_penalty`, `dry_multiplier` and
`stop`. `/reasoning on|off|auto` is a shortcut. `/reset context` returns to the
original sample prompt and answer while preserving overrides; `/reset all` also
restores the captured request parameters. Model, context size and thread counts
are displayed by `/settings` as read-only server parameters. No conversation
statistics are displayed.

The CMD launcher pins the generating Python and library location, with the report
addressed relative to its neighbouring launcher. Moving/removing that interpreter
or library requires regenerating the launcher. The viewer can also be started with
`python -m zemi.report_viewer path/to/module.dataset.md`.

## Console output and server logs

Managed llama.cpp stdout/stderr are redirected by the shared Arsenal startup path to unique files under `@inst/_tmp/arsenal-logs/`, for both notebook execution and terminal chats. The console shows startup status and the log path. Launch failures/timeouts include the last 4096 bytes of the log with ANSI sequences removed. External server output is not controlled by ZEMI.

The terminal prints response content incrementally via OpenAI streaming. Complete successful responses are appended and saved only after the finish event; broken streams and tool requests leave the previous conversation intact. A final line shows server-reported completion tokens and total request duration, including input processing. Stream chunks are text fragments, not necessarily individual tokens; chunk count is never presented as token count. Notebook calls keep their existing non-streaming request and capture behavior.
