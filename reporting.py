"""Job-scoped Markdown reporting. Clients render fragments; the writer owns files."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from collections.abc import Mapping, Sequence


def _cell(value):
    if value is None:
        return "—"
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers, rows):
    return "\n".join(("| " + " | ".join(headers) + " |",
                      "|" + "|".join("---" for _ in headers) + "|",
                      *("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)))


def _link(label, href):
    return f"[{_cell(label)}]({href})" if href else _cell(label)


def _count(rows):
    return f"{sum(row.get('status') == 'succeeded' for row in rows)} / {len(rows)}"


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
        ref = self._register(key, "samples/", f"{module_id}.sample-{sample_id}")
        self._save(key)
        return ref

    def register_run(self, module_id, run_id, *, sample_id=None):
        key = ("run", module_id, run_id)
        ref = self._register(key, "runs/", f"{module_id}.run-{run_id}")
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
        self._fragments.setdefault(key, {})[fragment] = content.strip()
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
            title, order = f"Worksheet Detection: {key[2]}", ("worksheet_detection_report",)
        elif key[0] == "sample":
            title, order = f"Sample: {key[2]}", ("sample_trial",)
        else:
            title, order = f"Run: {key[2]}", ("run_report",)
        navigation = []
        if key[0] != "job":
            navigation.append(_link("Back to job report", self.href(ref, self.ref("job"))))
            if len(key) > 1 and key[0] != "module":
                navigation.append(_link("Module Report", self.href(ref, self.ref("module", key[1]))))
            if key[0] == "run":
                parent = self._parents.get(key)
                if parent is not None:
                    navigation.append(_link("Sample Report", self.href(ref, self.ref("sample", key[1], parent))))
            if key[0] == "item":
                navigation.append(_link("Dataset Report", self.href(ref, self.ref("dataset", key[1]))))
            if key[0] == "sample":
                for run_key, parent in self._parents.items():
                    if run_key[1] == key[1] and parent == key[2]:
                        navigation.append(_link(f"Run {run_key[2]}", self.href(ref, self._refs[run_key])))
        body = [f"# {title}", ""]
        if navigation:
            body.extend((" · ".join(navigation), ""))
        for name in order:
            body.extend((fragments.get(name) or ("Pending." if name not in {"module_errors", "module_artifact_links"} else ""), ""))
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        os.replace(tmp, target)

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
                ("Maximum trials", config.get("max_trials")), ("Dataset items", item_count)]
        if dataset_ref and writer:
            rows.append(("Dataset", _link("Dataset Report", writer.href(writer.ref("module", module_id), dataset_ref))))
        text = "## Configuration\n\n" + _table(("Setting", "Value"), rows)
        dimensions = {d.name for d in space.dimensions}
        text += "\n\n" + _table(("Parameter", "Role", "Value"), ((k, "Variable" if k in dimensions else "Fixed", v) for k, v in space.start.values.items()))
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
                   "Metrics<br>" + (" / ".join(metrics) or "—"), "Runs<br>(OK / Total)", "Duration")
        rows = []
        for number, sample in enumerate(samples, 1):
            sid = sample["id"]
            rows.append((_link(number, writer.href(source, writer.ref("sample", module_id, sid))),
                " / ".join(_cell(sample.get("params", {}).get(k)) for k in param_names) or "—",
                sample.get("score"), sample.get("status"),
                " / ".join(_cell(sample.get("metrics", {}).get(k)) for k in metrics) or "—",
                _link(_count(sample.get("runs", [])), writer.href(source, writer.ref("module_runs", module_id), writer.sample_anchor(sid))),
                sample.get("duration")))
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
              sample.get("metrics"), sample.get("params"))]))

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
        return ("## Parameters\n\n" + _table(("Parameter", "Value"), params.items()) + "\n\n## Evaluation\n\n" +
            _table(("Score", "Metrics", "Feedback"), [(score, metrics, feedback)]))

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
        for number, sample in enumerate(samples, 1):
            sid = sample["id"]
            parts.append(f"### Sample {number}\n\nSample ID: `{sid}`")
            rows = [(_link(run["run_id"], writer.href(source, writer.ref("run", module_id, run["run_id"]))),
                     _link(number, writer.href(source, writer.ref("sample", module_id, sid))),
                     run.get("status"), run.get("duration")) for run in sample.get("runs", [])]
            parts.append(_table(("Run", "Sample", "Status", "Duration"), rows) if rows else "No runs started.")
        if not samples: parts.append("No runs started.")
        return "\n\n".join(parts)

    def render_trial_dataset(self, *, dataset, history, writer, module_id):
        source = writer.ref("dataset", module_id)
        rows = []
        for item in dataset.items:
            found = [run for trial in history for run in trial.runs if run.get("dataset_item_id") == item["id"]]
            ok = sum(run.get("metrics", {}).get("exact_match") is True for run in found)
            total = len(found)
            workbook = item.get("input", {}).get("workbook_path")
            href = writer.artifact_href(source, __import__("zemi.dataset", fromlist=["zemi_path"]).zemi_path(workbook)) if workbook else None
            rows.append((_link(item["id"], href), _link(f"{ok} / {total}", writer.href(source, writer.ref("item", module_id, item["id"]))),
                         f"{ok / total:.0%}" if total else "—"))
        return (f"**Job run ID:** `{writer.root.name}` · **Items:** {len(dataset.items)} · **Samples:** {len(history)}\n\n" +
            "## Items\n\n" + _table(("Item ID", "Worksheets detected<br>(OK / Total)", "Worksheet detection rate"), rows))

    def render_worksheet_detection_report(self, *, dataset, item, history, writer, module_id):
        source = writer.ref("item", module_id, item["id"])
        found = [(i, trial, run) for i, trial in enumerate(history, 1) for run in trial.runs if run.get("dataset_item_id") == item["id"]]
        ok = sum(run.get("metrics", {}).get("exact_match") is True for _, _, run in found)
        total = len(found)
        input_data = item.get("input", {})
        workbook = input_data.get("workbook_path")
        href = writer.artifact_href(source, __import__("zemi.dataset", fromlist=["zemi_path"]).zemi_path(workbook)) if workbook else None
        rows = []
        for number, trial, run in found:
            sid, rid = getattr(trial, "report_sample_id", None), run.get("run_id")
            ranges = run.get("prediction", {}).get("ranges") if isinstance(run.get("prediction"), Mapping) else None
            exact = run.get("metrics", {}).get("exact_match")
            rows.append((_link(number, writer.href(source, writer.ref("sample", module_id, sid))),
                _link(rid, writer.href(source, writer.ref("run", module_id, rid))),
                ranges if ranges is not None else "—", "Error" if run.get("error") or run.get("evaluation_error") else "Yes" if exact else "No"))
        return (f"**Item ID:** `{item['id']}` · **Workbook:** {_link(Path(workbook).name, href) if workbook else '—'} · "
                f"**Worksheet:** {_cell(input_data.get('worksheet_name'))}  \n**Job run ID:** `{writer.root.name}`\n\n"
                f"**Expected ranges:** {_cell(item.get('ground_truth'))}\n\n## Worksheet Detection Summary\n\n"
                f"Worksheets detected: {ok} / {total} · Worksheet detection rate: {f'{ok / total:.0%}' if total else '—'}\n\n"
                "## Worksheet Detections\n\n" + (_table(("Sample", "Run", "Detected ranges", "Exact match"), rows) if rows else "No checks started."))


class JobReporting:
    """Connect the existing execution lifecycle to one shared writer."""

    def __init__(self, component):
        self.component = component
        self.writer = ReportWriter(component.run_directory)
        self.renderer = DefaultReportRenderer()
        self._history = {}
        self._datasets = {}
        self._sample_trials = {}
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
        if data.get("duration_seconds") is not None: return f"{data['duration_seconds']:.1f} s"
        try:
            from datetime import datetime
            start = datetime.fromisoformat(data["started_at"])
            end = datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else datetime.now(start.tzinfo)
            return f"{(end-start).total_seconds():.1f} s"
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
                outputs = {"HTML": single.get("output_html"), "IPYNB": single.get("output_notebook")} if single else {}
                outputs = {k: v for k, v in outputs.items() if v and (writer.root / v).is_file()}
                if single:
                    rid = single["trial_id"]
                    writer.register_run(mid, rid)
                    writer.write_run_report(mid, rid, renderer.render_run_report(run={
                        "run_id": rid, "status": status, "prediction": single.get("output_params"),
                        "error": single.get("error"), "artifacts": outputs}, writer=writer, module_id=mid))
                    outputs["Run Report"] = writer.ref("run", mid, rid).path
                errors = [single["error"]] if single and single.get("error") else []
                summary.append({"id": mid, "name": Path(module.playbook_name).name, "optimized": False,
                                "status": status, "duration": duration, "outputs": outputs})
            writer.write_module_artifact_links(mid, renderer.render_module_artifact_links(
                artifacts=outputs, writer=writer, module_id=mid))
            writer.write_module_errors(mid, renderer.render_module_errors(errors=errors))
        writer.write_module_summary(renderer.render_module_summary(modules=summary, writer=writer))

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

    def write_run(self, module_id, sample_id, run, sample_trial):
        rid = run["run_id"]
        self._sample_trials[(module_id, sample_id)] = sample_trial
        self.writer.register_run(module_id, rid, sample_id=sample_id)
        self.writer.write_run_report(module_id, rid, sample_trial.render_run_report(
            run, writer=self.writer, module_id=module_id, sample_id=sample_id), sample_id=sample_id)
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
                    self.write_run(module_id, sample_id, run, sample_trial)
        fragment = dataset.render_report(history)
        self.writer.write_trial_dataset(module_id, fragment.markdown if hasattr(fragment, "markdown") else fragment)
        for item in dataset.items:
            self.writer.write_worksheet_detection_report(module_id, item["id"],
                dataset.render_worksheet_detection_report(item, history))
        self.refresh()
