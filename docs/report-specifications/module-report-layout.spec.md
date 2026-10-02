# Module Report Layout

Status: accepted. Updated: 2026-10-02.

This is the authoritative visual layout contract for the HTML Module Report,
its CMD viewer, Codex MCP App and embedded inline HTML. It complements module-report.spec.md.
Moving an existing Items table into a Module Report must preserve this contract.

## Section order

For optimized modules, Samples is the first table, followed by Items, then
configuration, execution metadata and other sections. One module has one CMD
launcher. MD and JSON carry the same report data; HTML provides scrolling,
sticky columns and native disclosures.

## Shared sizing

- Tables use intrinsic content width. Do not stretch them to the window width
  or distribute unused width among columns, even with only one sample.
- Each table has its own horizontal scroll container. Headers remain visible
  when scrolling vertically in the local viewer.
- In interactive HTML, show at most 10 body rows before internal vertical
  scrolling. Calculate the height from the header and the first 10 rendered
  rows, independently for each table. Shorter tables use their natural height.
  Apply the same rule in CMD and Codex, including after report navigation.
- Adjacent columns have ordinary cell padding, with no artificial spacers.
- Dark background, cyan links, green exact-match checks, red error labels.
- The standalone HTML, CMD viewer and inline HTML use the same table classes
  and sizing rules. Inline rendering may adapt the vertical scroll container.

## Samples

- Slash-separated metric names and their values use two aligned lines, split
  after the first half of the metric list. Preserve metric order and place
  the slash at the end of the first line. Apply this to headers and cells,
  including result tables in sample/item reports. Do not truncate metrics.

- First column is the one-based Sample number linking to its Sample Report.
- It has a 38 px minimum footprint and 4 px horizontal padding; its width grows
  only when required by its header or number, never to 290 px.
- Parameters contain short prompt names, not complete encoding configurations.
- Score, status, metrics, run counts, tokens and duration follow the renderer's
  published schema. Metrics names and values retain the agreed slash format.
- Do not apply the Items column widths or a generic sticky first-column rule
  to Samples. Long content in another column must not widen the number column.

## Items

- Order: #, Item ID, Target, Matches, Sample 1, Sample 2, ... .
- # is 38 px and links to the Item Report. Item ID links to the source workbook.
- #, Item ID and Target remain visible during horizontal scrolling.
- Item ID and Target are each fixed at 150 px including padding.
  Their inner spans are capped at 130 px.
  Overflow uses an ellipsis; the complete ID and target are available through
  title on hover. Do not grow these columns to fit long identifiers or targets.
  Target's sticky offset follows the actual Item ID column width.
- Matches and Sample columns sit immediately beside Target. No minimum width
  of 100% is allowed on this table or inherited from a shared table rule.
- Each Sample header contains only Sample N and the score on the next line.
  Highlight all tied highest-score samples in green. Do not append prompt names.
- Exact match is a green check with no repeated prediction. A single incorrect
  range is plain text. Multiple results show the first result with an ellipsis
  and expand in place. Errors disclose the reason and raw model response.
- Sample chat actions work in the CMD viewer and Codex MCP App when an actual
  captured context supports them. They do not alter the column mapping.
- Chat links must be visible at the top level of every supported sample cell:
  the checkmark, range or Error label itself is the link. Do not put a separate
  "Continue in terminal" link inside expanded details or add a text label to
  the table. For details cells the label opens chat and the native disclosure
  arrow expands/collapses the details in place.

## Regression checks

Render a combined report with both tables, including a partial run with one
sample. Verify Samples is classified separately and its number is narrow;
Items retains its order, sticky classes and bounded Target value. Verify no
shared rule stretches tables or gives every first column 290 px. Preserve
disclosures, best-score markup and numbered chat mapping.
