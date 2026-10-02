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
- Adjacent columns have ordinary cell padding, with no artificial spacers.
- Dark background, cyan links, green exact-match checks, red error labels.
- The standalone HTML, CMD viewer and inline HTML use the same table classes
  and sizing rules. Inline rendering may adapt the vertical scroll container.

## Samples

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
- Item ID is 290 px, with truncation and full text available through its title.
- Target uses only its content width, capped at 260 px through a bounded inner
  span; longer text is truncated, with the full value available through its title.
- Matches and Sample columns sit immediately beside Target. No minimum width
  of 100% is allowed on this table or inherited from a shared table rule.
- Each Sample header contains only Sample N and the score on the next line.
  Highlight all tied highest-score samples in green. Do not append prompt names.
- Exact match is a green check with no repeated prediction. A single incorrect
  range is plain text. Multiple results show the first result with an ellipsis
  and expand in place. Errors disclose the reason and raw model response.
- Sample chat actions are available in the native viewer only when an actual
  captured context supports them. They do not alter the column mapping.

## Regression checks

Render a combined report with both tables, including a partial run with one
sample. Verify Samples is classified separately and its number is narrow;
Items retains its order, sticky classes and bounded Target value. Verify no
shared rule stretches tables or gives every first column 290 px. Preserve
disclosures, best-score markup and numbered chat mapping.
