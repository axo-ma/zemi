# Encoding and prompt packages

A component keeps each experiment together. Its `params/` directory contains
one package per launch variant:

```text
experiment6/params/stage3_markdown/
    params.toml
    prompts.md
    encoder.py
```

The job selects that `params.toml`. The playbook and dataset may be shared
between packages. No new structural sections are introduced in Params 0.6.

## Paired parameter values

```toml
[modules.params]
encoding_prompt = { values = [
    { prompt_name = "cells", prompt_file = "@comp/params/encoding_example/prompts.md", encoder = "@comp/params/encoding_example/encoder.py:encode", encoding_format = "cells" },
    { prompt_name = "cells_compact", prompt_file = "@comp/params/encoding_example/prompts.md", encoder = "@comp/params/encoding_example/encoder.py:encode", encoding_format = "cells_compact" },
], start = { prompt_name = "cells", prompt_file = "@comp/params/encoding_example/prompts.md", encoder = "@comp/params/encoding_example/encoder.py:encode", encoding_format = "cells" } }
```

`values` and `start` follow the existing ParamSpace contract. Each object is
one indivisible choice; its fields do not become independent dimensions.
A fixed `encoding_prompt` object is supported too.

The configured `prompt_name` is the authoritative base for Sample names.
Suffixes start at `001` independently per prompt, assigned by configured grid
order including other parameter dimensions. Names do not depend on which
samples the optimizer executes first. Dataset items generate Runs of the same
Sample. Legacy Modules without `encoding_prompt` retain their technical IDs.

## Prompt source

`prompts.md` contains `# prompt_name` sections. Each section holds the complete
template, including examples and exactly one `{{item}}` placeholder. Internal
headings use level two or deeper. Headings inside fenced code are literal text.
Duplicate names, missing sections, malformed fences and missing/duplicate
placeholders are rejected. Names use letters, digits, underscores, dots and
hyphens. A name may not refer to different prompt files in one Module review.

## Encoder contract and playbook

```python
def encode(workbook_path: str | Path, worksheet_name: str, *, format: str) -> str:
    ...
```

The encoder owns reading the workbook and closing resources. It has access to
the complete file, including styles, formulas and merged cells when required
by its formats. It does not depend on ZEMI, prompts, models or reports.

```python
from zemi.prompting import build_prompt

item_text, prompt = build_prompt(
    encoding_prompt,
    dataset_input['workbook_path'],
    dataset_input['worksheet_name'],
)
```

ZEMI resolves paths with `env.path`, loads the chosen callable and passes the
format. The function must return text. This helper makes no model calls.

## Review and examples

`zemi.review.configure_review` reads configured bindings automatically. Review
snapshots contain the selected templates, prompt files and encoder source, in
addition to the job, params, playbook and provenance. No prompt reconstruction
from experiment-specific Python code is needed.

The consumer template supports `encoding_prompt` in its playbook and includes
`params/encoding_example/`. Its example expects a user-supplied
`data/input.xlsx`, worksheet `Sheet1`. It is an optional concrete-parameter
launch example, not a replacement for the default Hello request.
Canonical files are in [examples/encoding_prompt](../examples/encoding_prompt/params.toml).
