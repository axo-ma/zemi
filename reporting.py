"""Job-scoped Markdown reporting. Clients render fragments; the writer owns files."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from collections.abc import Mapping, Sequence


class _Markdown(str):
    """A link assembled by the renderer, already escaped for a Markdown cell."""


def _report_value(value):
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {key: _report_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_report_value(item) for item in value]
    return value


def _summary_params(params):
    """Use the prompt name in result tables; preserve stored configuration."""
    return {name: value.get("prompt_name", value)
            if name == "encoding_prompt" and isinstance(value, Mapping) else value
            for name, value in params.items()}


def _cell(value):
    if isinstance(value, _Markdown):
        return str(value)
    if value is None:
        return "—"
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(_report_value(value), ensure_ascii=False, sort_keys=True)
    elif isinstance(value, float):
        value = f"{value:.3f}"
    return html.escape(str(value), quote=False).replace("|", "\\|").replace("\n", " ")


def _table(headers, rows):
    return "\n".join(("| " + " | ".join(headers) + " |",
                      "|" + "|".join("---" for _ in headers) + "|",
                      *("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)))


def _link(label, href):
    return _Markdown(f"[{_cell(label)}]({href})") if href else _cell(label)


def _count(rows):
    return f"{sum(row.get('status') == 'succeeded' for row in rows)} / {len(rows)}"


def _duration_text(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s"


def _mean_output(runs, name):
    values = [(run.get("prediction") or {}).get(name) for run in runs
              if isinstance(run.get("prediction"), Mapping)]
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return sum(values) / len(values) if values else None


def _lm_duration(runs):
    values = [run['prediction'].get('lm_time') for run in runs if isinstance(run.get('prediction'), Mapping)]
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return _duration_text(sum(values)) if values else None


def _comparison(run):
    return run.get("comparison_prediction", run.get("prediction"))


def _exact(run):
    return (run.get("metrics", {}).get("exact_match") is True and run.get("status") == "succeeded"
            and not run.get("error") and not run.get("evaluation_error"))


def _short_value(value, href=None):
    cell = _cell(value)
    if href and len(cell) > 60:
        return _Markdown(cell[:60] + _link("...", href))
    return _Markdown(cell)


def _error_response(run):
    prediction = run.get("prediction")
    if isinstance(prediction, Mapping) and "raw_response" in prediction:
        return prediction["raw_response"]
    return prediction


def _response_text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _response_preview(value, href=None):
    if value is None:
        return "—"
    text = _response_text(value).replace("\r", " ").replace("\n", " ")
    truncated = bool(href) and len(text) > 60
    if truncated:
        text = text[:60]
    fence = "`" * (max((len(part) for part in re.findall(r"`+", text)), default=0) + 1)
    code = fence + " " + text.replace("|", "\\|") + " " + fence
    return _Markdown(code + (_link("...", href) if truncated else ""))


def _prediction_cell(run, href=None, show_error_response=False):
    if run.get("error") or run.get("evaluation_error"):
        if show_error_response:
            return _Markdown(_link("Error", href) + " · " + _response_preview(_error_response(run), href))
        return _link("Error", href)
    if _exact(run):
        return "✅"
    return _short_value(_comparison(run), href)


def _result_rows(runs, *, writer=None, module_id=None, source=None, include_target=False, include_sample=False):
    metrics = sorted({key for run in runs for key in run.get("metrics", {})})
    headers = (["Sample"] if include_sample else ["Item ID"]) + ["Run"]
    if include_target:
        headers.append("Target")
    headers += ["Prediction", "Metrics<br>" + (" / ".join(metrics) or "—"), "Error"]
    rows = []
    for run in runs:
        item_id = run.get("dataset_item_id", (run.get("item") or {}).get("id"))
        rid = run.get("run_id")
        item_href = writer.href(source, writer.ref("item", module_id, item_id)) if writer and source else None
        run_href = writer.href(source, writer.ref("run", module_id, rid)) if writer and source else None
        if include_sample:
            sid = run.get("report_sample_id")
            first = _link(run.get('report_sample_label', f"Sample {run.get('report_sample_number')}"),
                          writer.href(source, writer.ref("sample", module_id, sid)) if writer and source else None)
        else:
            first = _link(item_id, item_href)
        row = [first, _link(rid, run_href)]
        if include_target:
            row.append((run.get("item") or {}).get("ground_truth"))
        row += [_prediction_cell(run, href=None if include_sample else run_href),
                " / ".join(_cell(run.get("metrics", {}).get(key)) for key in metrics) or "—",
                run.get("error") or run.get("evaluation_error")]
        rows.append(row)
    return _table(headers, rows) if rows else "No runs available."


def _replace_report(tmp: Path, target: Path) -> None:
    """Keep replacement atomic while tolerating brief Windows file locks."""
    for attempt, delay in enumerate((0.02, 0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0)):
        try:
            os.replace(tmp, target)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 8:
                raise
            time.sleep(delay)


@dataclass(frozen=True)
class ReportRef:
    path: str


class ReportWriter:
    """One writer for a job; atomic, ordered, replacing fragment writes."""

    _single = ("module_parameters", "module_results", "module_artifact_links", "module_errors")
    _optimized = ("module_optimization_config", "module_execution_summary",
                  "module_samples_summary", "module_selected_sample",
                  "module_optimization_progress", "module_artifact_links", "module_errors")

    def __init__(self, run_directory: Path):
        self.root = Path(run_directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._refs = {("job",): ReportRef("index.md")}
        self._fragments = {}
        self._used = {"index.md"}
        self._modules = {}
        self._parents = {}
        self._sample_anchors = {}
        self._secrets = set()

    def add_secrets(self, values):
        def collect(value):
            if isinstance(value, Mapping):
                for item in value.values(): collect(item)
            elif isinstance(value, (list, tuple)):
                for item in value: collect(item)
            elif value is not None and str(value):
                self._secrets.add(str(value))
        for value in values: collect(value)

    def _safe(self, identifier):
        raw = str(identifier)
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-._")[:70] or "item"
        return slug

    def _register(self, key, directory, stem, suffix=".md"):
        if key in self._refs:
            return self._refs[key]
        base = self._safe(stem)
        candidate = f"{directory}{base}{suffix}"
        if candidate.casefold() in {p.casefold() for p in self._used}:
            digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:10]
            candidate = f"{directory}{base}-{digest}{suffix}"
        if candidate.casefold() in {p.casefold() for p in self._used}:
            raise ValueError(f"Report filename collision: {key!r}")
        self._used.add(candidate)
        ref = ReportRef(candidate)
        self._refs[key] = ref
        return ref

    def register_module(self, module_id, *, filename=None, optimized=False):
        key = ("module", module_id)
        if key in self._refs:
            return self._refs[key]
        ref = self._register(key, "", module_id)
        self._modules[module_id] = {"filename": filename or str(module_id), "optimized": optimized}
        self._save(key)
        if optimized:
            self.register_module_runs(module_id)
        return ref

    def register_module_runs(self, module_id):
        ref = self._register(("module_runs", module_id), "", module_id, ".runs.md")
        self._save(("module_runs", module_id))
        return ref

    def register_dataset(self, module_id):
        ref = self._register(("dataset", module_id), "", module_id, ".dataset.md")
        self._save(("dataset", module_id))
        return ref

    def register_review(self, module_id):
        ref = self._register(("review", module_id), "", module_id, ".reproduction.md")
        self._save(("review", module_id))
        return ref

    def register_item(self, module_id, item_id):
        key = ("item", module_id, item_id)
        ref = self._register(key, "dataset-items/", f"{module_id}.{item_id}")
        self._save(key)
        return ref

    def register_sample(self, module_id, sample_id):
        key = ("sample", module_id, sample_id)
        if key not in self._refs:
            number = 1 + sum(k[0] == "sample" and k[1] == module_id for k in self._refs)
            self._sample_anchors[sample_id] = f"sample-{number}"
        stem = str(sample_id) if str(sample_id).startswith(f"{module_id}-sample-") else f"{module_id}-{sample_id}" if str(sample_id).startswith("sample-") else f"{module_id}-sample-{sample_id}"
        ref = self._register(key, "samples/", stem)
        self._save(key)
        return ref

    def register_run(self, module_id, run_id, *, sample_id=None):
        if self.ref("module_runs", module_id) is None:
            self.register_module_runs(module_id)
        key = ("run", module_id, run_id)
        if key in self._refs:
            return self._refs[key]
        stem = str(run_id) if str(run_id).startswith(f"{module_id}-run-") else f"{module_id}-{run_id}" if str(run_id).startswith("run-") else f"{module_id}-run-{run_id}"
        ref = self._register(key, "runs/", stem)
        self._parents[key] = sample_id
        self._save(key)
        if sample_id is not None and ("sample", module_id, sample_id) in self._refs:
            self._save(("sample", module_id, sample_id))
        return ref

    def ref(self, kind, module_id=None, identity=None):
        key = (kind,) if kind == "job" else (kind, module_id) if identity is None else (kind, module_id, identity)
        return self._refs.get(key)

    def href(self, source: ReportRef, target: ReportRef | None, anchor: str | None = None):
        if target is None or not (self.root / target.path).is_file():
            return None
        relative = os.path.relpath(self.root / target.path, (self.root / source.path).parent).replace("\\", "/")
        return quote(relative, safe="/.-_") + ("#" + quote(anchor, safe="-_") if anchor else "")

    def artifact_href(self, source: ReportRef, artifact):
        if not artifact:
            return None
        target = (self.root / artifact).resolve() if not Path(artifact).is_absolute() else Path(artifact).resolve()
        if not target.is_file():
            return None
        return quote(os.path.relpath(target, (self.root / source.path).parent).replace("\\", "/"), safe="/.-_")

    def sample_anchor(self, sample_id):
        return self._sample_anchors[sample_id]

    def _write(self, key, fragment, content):
        if not isinstance(content, str):
            raise TypeError("md_fragment must be a Markdown string")
        for secret in sorted(self._secrets, key=len, reverse=True):
            content = content.replace(secret, "***")
        content = content.strip()
        fragments = self._fragments.setdefault(key, {})
        if fragments.get(fragment) == content and (self.root / self._refs[key].path).is_file():
            return self._refs[key]
        fragments[fragment] = content
        self._save(key)
        return self._refs[key]

    def _save(self, key):
        ref = self._refs[key]
        target = self.root / ref.path
        target.parent.mkdir(parents=True, exist_ok=True)
        fragments = self._fragments.get(key, {})
        if key[0] == "job":
            title, order = "Job Report", ("job_header", "module_summary")
        elif key[0] == "module":
            module_id = key[1]
            title = f"Module: {self._modules[module_id]['filename']}"
            order = ("module_header",) + (self._optimized if self._modules[module_id]["optimized"] else self._single)
        elif key[0] == "module_runs":
            title, order = f"Module Runs: {self._modules[key[1]]['filename']}", ("module_runs_summary",)
        elif key[0] == "dataset":
            title, order = f"Dataset: {self._modules[key[1]]['filename']}", ("trial_dataset",)
        elif key[0] == "item":
            title, order = f"Dataset Item: {key[2]}", ("worksheet_detection_report",)
        elif key[0] == "review":
            title, order = f"Reproduction: {key[1]}", ("review_report",)
        elif key[0] == "sample":
            title, order = f"Sample: {key[2]}", ("sample_trial",)
        else:
            title, order = f"Run: {key[2]}", ("run_report",)
        navigation = []
        if key[0] in {"sample", "module_runs"}:
            navigation.append(_link("Back to Module Report", self.href(ref, self.ref("module", key[1]))))
        elif key[0] == "run":
            navigation.append(_link("Back to Runs Report", self.href(ref, self.ref("module_runs", key[1]))))
        elif key[0] == "item":
            navigation.append(_link("Back to Dataset Report", self.href(ref, self.ref("dataset", key[1]))))
        elif key[0] != "job":
            navigation.append(_link("Back to job report", self.href(ref, self.ref("job"))))
            if len(key) > 1 and key[0] != "module":
                navigation.append(_link("Module Report", self.href(ref, self.ref("module", key[1]))))
        body = [f"# {title}", ""]
        if navigation:
            body.extend((" · ".join(navigation), ""))
        for name in order:
            body.extend((fragments.get(name) or ("Pending." if name not in {"module_errors", "module_artifact_links"} else ""), ""))
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        _replace_report(tmp, target)

    def write_job_header(self, md_fragment): return self._write(("job",), "job_header", md_fragment)
    def write_module_summary(self, md_fragment): return self._write(("job",), "module_summary", md_fragment)
    def write_module_header(self, module_id, md_fragment): return self._write(("module", module_id), "module_header", md_fragment)
    def write_module_parameters(self, module_id, md_fragment): return self._write(("module", module_id), "module_parameters", md_fragment)
    def write_module_results(self, module_id, md_fragment): return self._write(("module", module_id), "module_results", md_fragment)
    def write_module_optimization_config(self, module_id, md_fragment): return self._write(("module", module_id), "module_optimization_config", md_fragment)
    def write_module_execution_summary(self, module_id, md_fragment): return self._write(("module", module_id), "module_execution_summary", md_fragment)
    def write_module_samples_summary(self, module_id, md_fragment): return self._write(("module", module_id), "module_samples_summary", md_fragment)
    def write_module_selected_sample(self, module_id, md_fragment): return self._write(("module", module_id), "module_selected_sample", md_fragment)
    def write_module_optimization_progress(self, module_id, md_fragment): return self._write(("module", module_id), "module_optimization_progress", md_fragment)
    def write_module_artifact_links(self, module_id, md_fragment): return self._write(("module", module_id), "module_artifact_links", md_fragment)
    def write_module_errors(self, module_id, md_fragment): return self._write(("module", module_id), "module_errors", md_fragment)
    def write_sample_trial(self, module_id, sample_id, md_fragment): return self._write(("sample", module_id, sample_id), "sample_trial", md_fragment)
    def write_run_report(self, module_id, run_id, md_fragment, *, sample_id=None):
        key = ("run", module_id, run_id)
        if key not in self._refs: self.register_run(module_id, run_id, sample_id=sample_id)
        return self._write(key, "run_report", md_fragment)
    def write_module_runs_summary(self, module_id, md_fragment): return self._write(("module_runs", module_id), "module_runs_summary", md_fragment)
    def write_trial_dataset(self, module_id, md_fragment): return self._write(("dataset", module_id), "trial_dataset", md_fragment)
    def write_review_report(self, module_id, md_fragment): return self._write(("review", module_id), "review_report", md_fragment)
    def write_worksheet_detection_report(self, module_id, item_id, md_fragment): return self._write(("item", module_id, item_id), "worksheet_detection_report", md_fragment)


class DefaultReportRenderer:
    """Default client layouts; methods return fragments and never write files."""

    def render_job_header(self, *, job, component, run_id, status, started, duration):
        return (f"**Job:** `{job}` · **Component:** `{component}`  \n"
                f"**Job run ID:** `{run_id}` · **Status:** {_cell(status)}  \n"
                f"**Started:** {_cell(started)} · **Duration:** {_cell(duration)}")

    def render_module_summary(self, *, modules, writer):
        single = [m for m in modules if not m["optimized"]]
        optimized = [m for m in modules if m["optimized"]]
        source = writer.ref("job")
        parts = ["## Module Summary"]
        if single:
            rows = []
            for m in single:
                ref = writer.ref("module", m["id"])
                outputs = " · ".join(_link(label, writer.artifact_href(source, path))
                    for label, path in m.get("outputs", {}).items() if writer.artifact_href(source, path)) or "—"
                rows.append((_link(m["name"], writer.href(source, ref)), m["status"], m.get("duration"), outputs))
            parts.extend(("### Single Execution", _table(("Module", "Status", "Duration", "Output"), rows)))
        if optimized:
            rows = []
            for m in optimized:
                mid = m["id"]
                ref = writer.ref("module", mid)
                runs = writer.ref("module_runs", mid)
                selected = m.get("selected")
                result = writer.ref("sample", mid, selected) if selected else None
                rows.append((_link(m["name"], writer.href(source, ref)), f"`{m['mode']}`", m["status"],
                    _link(_count(m.get("samples", [])), writer.href(source, ref, "samples")),
                    _link(_count(m.get("runs", [])), writer.href(source, runs)),
                    _link(m.get("selected_number", "—"), writer.href(source, result)), m.get("score"), m.get("duration")))
            parts.extend(("### Execution With Optimizer", _table(("Module", "Mode", "Status", "Samples<br>(OK / Total)",
                "Runs<br>(OK / Total)", "Result", "Score", "Duration"), rows)))
        if not single and not optimized: parts.append("No modules configured.")
        return "\n\n".join(parts)

    def render_module_header(self, *, module_id, job, component, run_id, mode, status, started, duration):
        fields = {"Module ID": module_id, "Job": job, "Component": component, "Job run ID": run_id,
                  "Mode" if mode else "Execution": mode or "Single execution", "Status": status,
                  "Started": started, "Duration": duration}
        return _table(("Field", "Value"), fields.items())

    def render_module_parameters(self, *, params):
        return "## Parameters\n\n" + (_table(("Parameter", "Value"), params.items()) if params else "No parameters available.")

    def render_module_results(self, *, outputs):
        return "## Results\n\n" + (_table(("Output", "Value"), outputs.items()) if outputs else "No published output.")

    def render_module_optimization_config(self, *, config, space, dataset_ref=None, writer=None, module_id=None, item_count=None):
        rows = [("Optimizer", config.get("strategy")), ("Mode", config.get("mode")),
                ("Maximum trials", config.get("max_trials")), ("Dataset items", item_count),
                ("Reuse kernel", config.get("reuse_kernel", True))]
        for setting in ("seed", "blocks"):
            if setting in config:
                rows.append((setting.title(), config[setting]))
        if dataset_ref and writer:
            rows.append(("Dataset", _link("Dataset Report", writer.href(writer.ref("module", module_id), dataset_ref))))
        text = "## Configuration\n\n" + _table(("Setting", "Value"), rows)
        dimensions = {d.name for d in space.dimensions}
        text += "\n\n" + _table(("Parameter", "Role", "Value"), ((k, "Variable" if k in dimensions else "Fixed", None if k in dimensions else v) for k, v in space.start.values.items()))
        if config.get("mode") == "start_only": text += "\n\nThe configured search space was not explored."
        return text

    def render_module_execution_summary(self, *, samples, runs, stop_reason=None, writer, module_id):
        source = writer.ref("module", module_id)
        return "## Module Execution Summary\n\n" + _table(("Samples<br>(OK / Total)", "Runs<br>(OK / Total)", "Stop reason"),
            [(_link(_count(samples), writer.href(source, source, "samples")),
              _link(_count(runs), writer.href(source, writer.ref("module_runs", module_id))), stop_reason or "Running")])

    def render_module_samples_summary(self, *, samples, param_names, writer, module_id):
        source = writer.ref("module", module_id)
        metrics = sorted({name for sample in samples for name in sample.get("metrics", {})})
        headers = ("Sample", "Parameters<br>" + (" / ".join(param_names) or "—"), "Score", "Status",
                   "Metrics<br>" + (" / ".join(metrics) or "—"), "Runs<br>(OK / Total)", "Mean Tokens<br>(item / prompt)", "Duration<br>(module / LM)")
        rows = []
        for number, sample in enumerate(samples, 1):
            sid = sample["id"]
            rows.append((_link(number, writer.href(source, writer.ref("sample", module_id, sid))),
                " / ".join(_cell(_summary_params(sample.get("params", {})).get(k)) for k in param_names) or "—",
                sample.get("score"), sample.get("status"),
                " / ".join(_cell(sample.get("metrics", {}).get(k)) for k in metrics) or "—",
                _link(_count(sample.get("runs", [])), writer.href(source, writer.ref("module_runs", module_id), writer.sample_anchor(sid))),
                " / ".join(_cell(_mean_output(sample.get("runs", []), name)) for name in ("item_tokens", "prompt_tokens")),
                " / ".join(_cell(value) for value in (sample.get("duration"), _lm_duration(sample.get("runs", []))))))
        return "## Samples\n\n" + (_table(headers, rows) if rows else "No samples started.")

    def render_module_selected_sample(self, *, selected, samples, mode, writer, module_id):
        if not selected: return "## Selected Sample\n\nNo evaluated sample is available."
        sample = next((s for s in samples if s["id"] == selected), None)
        if sample is None: return "## Selected Sample\n\nNo evaluated sample is available."
        number = samples.index(sample) + 1
        prefix = "Starting sample; no search or comparison was performed." if mode == "start_only" else "Best available result."
        source = writer.ref("module", module_id)
        return ("## Selected Sample\n\n" + prefix + "\n\n" + _table(("Sample", "Score", "Metrics", "Parameters"),
            [(_link(number, writer.href(source, writer.ref("sample", module_id, selected))), sample.get("score"),
              sample.get("metrics"), _summary_params(sample.get("params", {})))]))

    def render_module_optimization_progress(self, *, mode, detail=None):
        return "## Module Optimization Progress\n\n" + ("No parameter search was performed." if mode == "start_only" else detail or "No optimization progress details available.")

    def render_module_artifact_links(self, *, artifacts, writer, module_id):
        source = writer.ref("module", module_id)
        rows = [(name, Path(path).suffix.lstrip(".").upper() or "File", _link(Path(path).name, writer.artifact_href(source, path)))
                for name, path in artifacts.items() if writer.artifact_href(source, path)]
        return "## Module Artifacts\n\n" + (_table(("Artifact", "Format", "File"), rows) if rows else "No output files available.")

    def render_module_errors(self, *, errors):
        return "## Errors\n\n" + ("\n\n".join(_cell(e) for e in errors) if errors else "No errors recorded.")

    def render_sample_trial(self, *, sample_trial, runs, metrics, score, feedback):
        params = sample_trial.param_sample.values if sample_trial.param_sample else {}
        parts = ["## Parameters", _table(("Parameter", "Value"), params.items()),
                 "## Evaluation", _table(("Score", "Metrics"), [(score, metrics)])]
        binding = params.get("encoding_prompt")
        if isinstance(binding, Mapping):
            prompt = getattr(sample_trial, "_report_prompt", None)
            if prompt is None:
                from .prompting import load_prompts
                prompt = load_prompts(binding["prompt_file"])[binding["prompt_name"]]
            from .review import _fence
            parts += ["## Prompt", _fence(prompt)]
        parts += ["## Runs",
                  _result_rows(runs, writer=getattr(sample_trial, "_report_writer", None),
                      module_id=getattr(sample_trial, "_report_module_id", None),
                      source=getattr(sample_trial, "_report_source", None), include_target=True)]
        return "\n\n".join(parts)

    def render_run_report(self, *, run, writer=None, module_id=None, sample_id=None):
        lines = [f"**Status:** {_cell(run.get('status'))}", f"**Dataset item:** {_cell(run.get('dataset_item_id'))}"]
        if run.get("error") or run.get("evaluation_error"):
            lines.append("## Errors\n\n" + _cell(run.get("error") or run.get("evaluation_error")))
        if run.get("prediction") is not None:
            lines.append("## Prediction\n\n```json\n" + json.dumps(run["prediction"], ensure_ascii=False, indent=2) + "\n```")
        if run.get("metrics"):
            lines.append("## Metrics\n\n" + _table(("Metric", "Value"), run["metrics"].items()))
        artifacts = run.get("artifacts", {})
        if writer and artifacts:
            source = writer.ref("run", module_id, run["run_id"])
            links = [_link(name, writer.artifact_href(source, path)) for name, path in artifacts.items() if writer.artifact_href(source, path)]
            if links: lines.append("## Artifacts\n\n" + " · ".join(links))
        return "\n\n".join(lines)

    def render_module_runs_summary(self, *, samples, writer, module_id):
        source = writer.ref("module_runs", module_id)
        parts = ["## Runs"]
        output_names = list(dict.fromkeys(
            name for sample in samples for run in sample.get("runs", [])
            for name in run.get("report_output_keys", [])))
        output_labels = {"lm_time": "LM Time", "item_tokens": "Item Tokens",
                         "prompt_tokens": "Prompt Tokens"}
        output_header = "Outputs<br>" + (" / ".join(output_labels.get(name, name) for name in output_names) or "—")
        for number, sample in enumerate(samples, 1):
            sid = sample["id"]
            sample_link = _link(f"Sample {number}", writer.href(source, writer.ref("sample", module_id, sid)))
            params = sample.get("params", {})
            parameters = " / ".join(f"{name} = {_cell(value)}" for name, value in params.items()) or "—"
            parts.append(f"### {sample_link}\n\nSample ID: `{sid}`\n\n**Parameters:** {parameters}")
            rows = [(_link(run["run_id"], writer.href(source, writer.ref("run", module_id, run["run_id"]))),
                     run.get("status"),
                     " / ".join(_cell((run.get("prediction") or {}).get(name))
                                if name in run.get("report_output_keys", []) else "—"
                                for name in output_names) or "—",
                     run.get("duration")) for run in sample.get("runs", [])]
            parts.append(_table(("Run", "Status", output_header, "Duration"), rows) if rows else "No runs started.")
        if not samples: parts.append("No runs started.")
        return "\n\n".join(parts)

    def render_trial_dataset(self, *, dataset, history, writer, module_id):
        source = writer.ref("dataset", module_id)
        sample_headers = []
        for number, trial in enumerate(history, 1):
            sid = getattr(trial, "report_sample_id", None)
            params = trial.sample.values if getattr(trial, "sample", None) else {}
            label = f"Sample {number}"
            binding = params.get("encoding_prompt")
            name = binding.get("prompt_name") if isinstance(binding, Mapping) else params.get("encoding_format")
            if name:
                label += f" ({name})"
            sample_headers.append(_link(label, writer.href(source, writer.ref("sample", module_id, sid))))
        rows = []
        for item in dataset.items:
            found = [run for trial in history for run in trial.runs if run.get("dataset_item_id") == item["id"]]
            comparable = any("exact_match" in run.get("metrics", {}) for run in found)
            matches = f"{sum(_exact(run) for run in found)} / {len(found)}" if comparable else "—"
            item_ref = writer.ref("item", module_id, item["id"])
            href = writer.href(source, item_ref)
            comparisons = []
            for trial in history:
                runs = [run for run in trial.runs if run.get("dataset_item_id") == item["id"]]
                if not runs:
                    comparisons.append("—")
                elif len(runs) == 1:
                    comparisons.append(_prediction_cell(runs[0], href=href, show_error_response=True))
                else:
                    comparisons.append(_short_value([_comparison(run) for run in runs], href))
            rows.append((_link(item["id"], href), matches, item.get("ground_truth"), *comparisons))
        return (f"**Job run ID:** `{writer.root.name}` · **Items:** {len(dataset.items)} · **Samples:** {len(history)}\n\n"
            "## Items\n\nTarget shows the expected result. ✅ means evaluator-confirmed exact match; — means no prediction. Errors and truncated values link to full item results.\n\n" +
            _table(("Item ID", "Matches", "Target", *sample_headers), rows))

    def render_worksheet_detection_report(self, *, dataset, item, history, writer, module_id):
        source = writer.ref("item", module_id, item["id"])
        found = []
        for number, trial in enumerate(history, 1):
            params = trial.sample.values if getattr(trial, "sample", None) else {}
            binding = params.get("encoding_prompt")
            name = binding.get("prompt_name") if isinstance(binding, Mapping) else params.get("encoding_format")
            label = f"Sample {number}" + (f" ({name})" if name else "")
            for run in trial.runs:
                if run.get("dataset_item_id") == item["id"]:
                    found.append(dict(run, report_sample_id=getattr(trial, "report_sample_id", None),
                                      report_sample_number=number, report_sample_label=label))
        from .review import _fence
        responses = []
        for run in found:
            response = _error_response(run)
            if (run.get("error") or run.get("evaluation_error")) and response is not None:
                responses += [f"### {run.get('run_id')}", _fence(_response_text(response))]
        raw_section = "\n\n## Raw responses for errors\n\n" + "\n\n".join(responses) if responses else ""
        return (f"**Item ID:** `{item['id']}` · **Job run ID:** `{writer.root.name}`\n\n"
                "## Input\n\n" + _table(("Parameter", "Value"), item.get("input", {}).items()) +
                "\n\n## Target\n\n" + _cell(item.get("ground_truth")) +
                "\n\n## Results\n\n" + _result_rows(found, writer=writer, module_id=module_id,
                    source=source, include_sample=True) + raw_section)


class JobReporting:
    """Connect the existing execution lifecycle to one shared writer."""

    def __init__(self, component):
        self.component = component
        self.writer = ReportWriter(component.run_directory)
        self.renderer = DefaultReportRenderer()
        self._history = {}
        self._datasets = {}
        self._sample_trials = {}
        self._reviews = {}
        for module in component.modules:
            self.writer.register_module(module.module_id, filename=Path(module.playbook_name).name,
                                        optimized=module.optimizer_config is not None)
            self.writer.add_secrets(module.params.get(name) for name in module.secret_param_names if name in module.params)
        self.refresh()

    def _parent(self, module_id):
        return next((p for p in self.component.report.data.get("job_trial", {}).get("playbook_trials", [])
                     if p["playbook_id"] == module_id), None)

    def _single_entry(self, module):
        return next((t for t in reversed(self.component.report.data["trials"])
                     if t.get("trial_id") == module.trial_id), None)

    def _samples(self, parent):
        result = []
        for item in parent.get("samples", []) if parent else []:
            runs = [dict(run, duration=self._duration(run)) for run in item.get("runs", [])]
            result.append({"id": item["sample_trial_id"], "params": item.get("params", {}),
                "score": item.get("score"), "metrics": item.get("metrics", {}),
                "status": item.get("status", "running"), "runs": runs,
                "duration": self._duration(item)})
        return result

    @staticmethod
    def _duration(data):
        if not data: return None
        if data.get("duration_seconds") is not None: return _duration_text(data["duration_seconds"])
        try:
            from datetime import datetime
            start = datetime.fromisoformat(data["started_at"])
            end = datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else datetime.now(start.tzinfo)
            return _duration_text((end-start).total_seconds())
        except (TypeError, ValueError, KeyError):
            return None

    def refresh(self):
        component, writer, renderer = self.component, self.writer, self.renderer
        report = component.report.data
        writer.add_secrets(component.report._secret_values)
        job = Path(__import__("sys").argv[0]).name
        if job in {"", "-c", "pytest", "__main__.py"}:
            job = component.params_path.name
        writer.write_job_header(renderer.render_job_header(job=job, component=component.name,
            run_id=component.run_directory.name, status=report["status"],
            started=report.get("started_at"), duration=self._duration(report)))
        summary = []
        for module in component.modules:
            mid = module.module_id
            parent = self._parent(mid) if module.optimizer_config else None
            single = self._single_entry(module) if not module.optimizer_config else None
            status = (parent or single or {}).get("status", "Not started" if module.enabled else "Skipped")
            started = (parent or single or {}).get("started_at")
            duration = self._duration(parent or single)
            writer.write_module_header(mid, renderer.render_module_header(module_id=mid, job=job,
                component=component.name, run_id=component.run_directory.name,
                mode=module.optimizer_config.get("mode") if module.optimizer_config else None,
                status=status, started=started, duration=duration))
            if module.optimizer_config:
                from .params import ParamSpace
                space = ParamSpace(config=module.config["_v05_space"])
                dataset = self._datasets.get(mid)
                writer.write_module_optimization_config(mid, renderer.render_module_optimization_config(
                    config=module.optimizer_config, space=space,
                    dataset_ref=writer.ref("dataset", mid) if dataset else None,
                    writer=writer, module_id=mid, item_count=len(dataset.items) if dataset else None))
                samples = self._samples(parent)
                runs = [run for sample in samples for run in sample["runs"]]
                selected = parent.get("best_sample") if parent else None
                writer.write_module_execution_summary(mid, renderer.render_module_execution_summary(
                    samples=samples, runs=runs, stop_reason=parent.get("status") if parent and parent.get("finished_at") else None,
                    writer=writer, module_id=mid))
                writer.write_module_samples_summary(mid, renderer.render_module_samples_summary(
                    samples=samples, param_names=[d.name for d in space.dimensions], writer=writer, module_id=mid))
                writer.write_module_selected_sample(mid, renderer.render_module_selected_sample(
                    selected=selected, samples=samples, mode=module.optimizer_config.get("mode"), writer=writer, module_id=mid))
                writer.write_module_runs_summary(mid, renderer.render_module_runs_summary(
                    samples=samples, writer=writer, module_id=mid))
                if module.optimizer_config.get("mode") == "start_only":
                    writer.write_module_optimization_progress(mid, renderer.render_module_optimization_progress(mode="start_only"))
                errors = [s["error"] for s in parent.get("samples", []) if s.get("error")] if parent else []
                outputs = {}
                if dataset:
                    outputs["Dataset Report"] = writer.ref("dataset", mid).path
                if mid in self._reviews:
                    from .review import render_review
                    writer.write_review_report(mid, render_review(self._reviews[mid], samples=samples,
                        report=report, module_id=mid, writer=writer, item_count=len(dataset.items) if dataset else None))
                    outputs["Reproduction Report"] = writer.ref("review", mid).path
                summary.append({"id": mid, "name": Path(module.playbook_name).name, "optimized": True,
                    "mode": module.optimizer_config.get("mode"), "status": status, "samples": samples,
                    "runs": runs, "selected": selected,
                    "selected_number": next((i for i, s in enumerate(samples, 1) if s["id"] == selected), None),
                    "score": next((s["score"] for s in samples if s["id"] == selected), None),
                    "duration": duration})
            else:
                params = single.get("input_params", {}) if single else {}
                writer.write_module_parameters(mid, renderer.render_module_parameters(params=params))
                writer.write_module_results(mid, renderer.render_module_results(outputs=single.get("output_params", {}) if single else {}))
                outputs = {"IPYNB": single.get("output_notebook")} if single else {}
                outputs = {k: v for k, v in outputs.items() if v and (writer.root / v).is_file()}
                if single:
                    rid = single["trial_id"]
                    writer.register_run(mid, rid)
                    writer.write_run_report(mid, rid, renderer.render_run_report(run={
                        "run_id": rid, "status": status, "prediction": single.get("output_params"),
                        "error": single.get("error"), "artifacts": outputs}, writer=writer, module_id=mid))
                    runs_ref = writer.ref("module_runs", mid)
                    writer.write_module_runs_summary(mid, "## Runs\n\n" + _table(("Run", "Status", "Duration"),
                        [(_link(rid, writer.href(runs_ref, writer.ref("run", mid, rid))), status, duration)]))
                    outputs["Runs Report"] = runs_ref.path
                    outputs["Run Report"] = writer.ref("run", mid, rid).path
                errors = [single["error"]] if single and single.get("error") else []
                summary.append({"id": mid, "name": Path(module.playbook_name).name, "optimized": False,
                                "status": status, "duration": duration, "outputs": outputs})
            writer.write_module_artifact_links(mid, renderer.render_module_artifact_links(
                artifacts=outputs, writer=writer, module_id=mid))
            writer.write_module_errors(mid, renderer.render_module_errors(errors=errors))
        writer.write_module_summary(renderer.render_module_summary(modules=summary, writer=writer))

    def register_review(self, module_id, snapshot):
        """Save the automatically collected launch snapshot."""
        self._reviews[module_id] = snapshot
        review_ref = self.writer.register_review(module_id)
        snapshot = self.writer.root / Path(review_ref.path).with_suffix(".json")
        def redact(value):
            if isinstance(value, str):
                for secret in sorted(self.writer._secrets, key=len, reverse=True):
                    value = value.replace(secret, "[REDACTED]")
                return value
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value
        self._reviews[module_id] = redact(self._reviews[module_id])
        content = json.dumps(self._reviews[module_id], ensure_ascii=False, indent=2)
        tmp = snapshot.with_name(f".{snapshot.name}.tmp")
        tmp.write_text(content + "\n", encoding="utf-8")
        _replace_report(tmp, snapshot)
        self.refresh()

    def register_dataset(self, module_id, dataset):
        self._datasets[module_id] = dataset
        dataset._report_writer = self.writer
        dataset._report_module_id = module_id
        self.writer.register_dataset(module_id)
        for item in dataset.items:
            self.writer.register_item(module_id, item["id"])
        self.refresh()

    def start_sample(self, module_id, sample_id):
        self.writer.register_sample(module_id, sample_id)
        self.writer.write_sample_trial(module_id, sample_id, "Sample execution in progress.")
        self.refresh()

    def write_run(self, module_id, sample_id, run, sample_trial, *, refresh=True):
        rid = run["run_id"]
        self._sample_trials[(module_id, sample_id)] = sample_trial
        sample_trial._report_writer = self.writer
        sample_trial._report_module_id = module_id
        sample_trial._report_source = self.writer.ref("sample", module_id, sample_id)
        binding = sample_trial.param_sample.values.get("encoding_prompt") if sample_trial.param_sample else None
        if isinstance(binding, Mapping):
            sample_trial._report_prompt = self._reviews.get(module_id, {}).get("prompts", {}).get(binding.get("prompt_name"))
        self.writer.register_run(module_id, rid, sample_id=sample_id)
        self.writer.write_run_report(module_id, rid, sample_trial.render_run_report(
            run, writer=self.writer, module_id=module_id, sample_id=sample_id), sample_id=sample_id)
        if refresh:
            self.refresh()

    def finish_sample(self, module_id, sample_id, text):
        self.writer.write_sample_trial(module_id, sample_id, text)
        self.refresh()

    def update_dataset(self, module_id, dataset, history):
        for trial in history:
            sample_id = getattr(trial, "report_sample_id", None)
            sample_trial = self._sample_trials.get((module_id, sample_id))
            if sample_trial:
                for run in trial.runs:
                    self.write_run(module_id, sample_id, run, sample_trial, refresh=False)
        fragment = dataset.render_report(history)
        self.writer.write_trial_dataset(module_id, fragment.markdown if hasattr(fragment, "markdown") else fragment)
        for item in dataset.items:
            self.writer.write_worksheet_detection_report(module_id, item["id"],
                dataset.render_worksheet_detection_report(item, history))
        self.refresh()
