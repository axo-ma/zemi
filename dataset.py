"""Dataset optimization adapters. Ground truth stays in the host evaluator."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, TypedDict

from . import env


class DatasetItem(TypedDict):
    """One evaluation example: Module input plus evaluator-only reference data."""

    id: str | int
    input: dict[str, Any]
    ground_truth: Any


@dataclass(frozen=True)
class TrialDataset:
    """A validated flat dataset whose in-memory shape matches its JSON shape."""
    items: list[DatasetItem]
    source: Path | None = None

    @classmethod
    def load(cls, path: str) -> "TrialDataset":
        source = zemi_path(path)
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"items"} or not isinstance(raw["items"], list):
            raise ValueError(f"{source}: expected exactly one items array")
        result, identities = [], set()
        for index, value in enumerate(raw["items"]):
            label = f"items[{index}]"
            if not isinstance(value, dict): raise ValueError(f"{label}: expected object")
            missing = {"id", "input", "ground_truth"} - set(value)
            unknown = set(value) - {"id", "description", "input", "ground_truth", "tags"}
            if missing or unknown: raise ValueError(f"{label}: missing {sorted(missing)}; unsupported {sorted(unknown)}")
            identity = value["id"]
            if not isinstance(identity, str) or not identity or identity in identities:
                raise ValueError(f"{label}.id: expected unique non-empty string")
            identities.add(identity)
            inputs = value["input"]
            if not isinstance(inputs, dict): raise ValueError(f"{label}.input: expected object")
            path_value, sheet = inputs.get("workbook_path"), inputs.get("worksheet_name")
            if path_value is not None or sheet is not None:
                if not isinstance(path_value, str) or not path_value.startswith(("@comp/", "@inst/")):
                    raise ValueError(f"{label}.input.workbook_path: expected @comp/... or @inst/...")
                if not zemi_path(path_value).is_file(): raise FileNotFoundError(path_value)
                if not isinstance(sheet, str) or not sheet: raise ValueError(f"{label}.input.worksheet_name: expected string")
            truth = value["ground_truth"]
            if not isinstance(truth, list): raise ValueError(f"{label}.ground_truth: expected array")
            truth = [exact_range(item) for item in truth]
            tags = value.get("tags", [])
            if len(truth) != len(set(truth)): raise ValueError(f"{label}.ground_truth: duplicate range")
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags): raise ValueError(f"{label}.tags: expected strings")
            if "description" in value and not isinstance(value["description"], str): raise ValueError(f"{label}.description: expected string")
            result.append(json.loads(json.dumps(value, ensure_ascii=False)))
        return cls(result, source)

    def render_report(self, *, history: Sequence[Any]):
        from .reporting import DefaultReportRenderer, ReportWriter
        class MemoryWriter(ReportWriter):
            def _save(self, key):
                pass
        writer = MemoryWriter(env.path.tmp / "standalone-reports")
        module_id = "dataset"
        writer.register_module(module_id)
        writer.register_dataset(module_id)
        for item in self.items:
            writer.register_item(module_id, item["id"])
        from types import SimpleNamespace
        trials = []
        for number, trial in enumerate(history, 1):
            sid = getattr(trial, "report_sample_id", None) or f"sample-{number}"
            writer.register_sample(module_id, sid)
            runs = []
            for index, original in enumerate(trial.runs, 1):
                rid = original.get("run_id") or f"{sid}-run-{index}"
                writer.register_run(module_id, rid, sample_id=sid)
                runs.append(dict(original, run_id=rid))
            trials.append(SimpleNamespace(sample=trial.sample, report_sample_id=sid, runs=runs))
        renderer = DefaultReportRenderer()
        markdown = renderer.render_trial_dataset(dataset=self, history=trials, writer=writer, module_id=module_id)
        details = {writer.ref("item", module_id, item["id"]).path:
                   renderer.render_worksheet_detection_report(dataset=self, item=item, history=trials,
                       writer=writer, module_id=module_id) for item in self.items}
        return markdown, details



@dataclass
class DatasetReport:
    markdown: str
    details: dict[str, str]
    path: str | None = None


class TableDetectionTrialDataset:
    """One configured table-detection dataset, loaded before optimization."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.items: list[DatasetItem] = []
        self.source: Path | None = None

    def load(self) -> None:
        loaded = TrialDataset.load(self.config["path"])
        self.items = loaded.items
        self.source = loaded.source

    def render_report(self, history: Sequence[Any]):
        writer = getattr(self, "_report_writer", None)
        module_id = getattr(self, "_report_module_id", None)
        if writer is None:
            markdown, details = TrialDataset(self.items, self.source).render_report(history=history)
            return DatasetReport(markdown, details)
        from .reporting import DefaultReportRenderer
        return DefaultReportRenderer().render_trial_dataset(dataset=self, history=history,
            writer=writer, module_id=module_id)

    def render_worksheet_detection_report(self, item, history):
        from .reporting import DefaultReportRenderer
        return DefaultReportRenderer().render_worksheet_detection_report(
            dataset=self, item=item, history=history, writer=self._report_writer,
            module_id=self._report_module_id)


def zemi_path(value):
    for prefix, root in (("@comp/", env.path.comp.root), ("@inst/", env.path.inst)):
        if isinstance(value, str) and value.startswith(prefix):
            candidate = (root / value[len(prefix):]).resolve()
            if candidate.is_relative_to(root.resolve()):
                return candidate
    raise ValueError(f"Expected a confined @comp/ or @inst/ path: {value!r}")


def relative_path(base, value, label):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or PureWindowsPath(value).drive or value.startswith(("@", "\\")):
        raise ValueError(f"{label}: expected a relative path")
    candidate = (base / value.replace("\\", "/")).resolve()
    if not candidate.is_relative_to(env.path.inst.resolve()):
        raise ValueError(f"{label}: path escapes ZEMI Instance")
    if not candidate.is_file():
        raise FileNotFoundError(f"{label}: file not found: {candidate}")
    return candidate


def exact_range(value):
    from openpyxl.utils.cell import range_boundaries
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]+[1-9][0-9]*:[A-Z]+[1-9][0-9]*", value):
        raise ValueError(f"Invalid exact range: {value!r}")
    c1, r1, c2, r2 = range_boundaries(value)
    if c1 > c2 or r1 > r2 or c2 > 16384 or r2 > 1048576:
        raise ValueError(f"Invalid exact range boundaries: {value!r}")
    return value


def _indexed(rows, label):
    if not isinstance(rows, list):
        raise ValueError(f"{label}: expected array")
    result = {}
    for i, row in enumerate(rows):
        key = row.get("id") if isinstance(row, dict) else None
        if isinstance(key, bool) or not isinstance(key, (str, int)) or key == "" or key in result:
            raise ValueError(f"{label}[{i}].id: missing, invalid or duplicate id")
        result[key] = row
    return result


def table_dataset(*, path, params):
    """Load the canonical flat table-detection dataset without normalization."""
    return TrialDataset.load(path).items


def _scores(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0}


def table_evaluator(trial, *, params):
    """Evaluate validated table ranges as sets, irrespective of order or repetition."""
    details = []
    tags = defaultdict(lambda: [0, 0, 0])
    total = [0, 0, 0]
    errors = empty = empty_correct = 0
    for run in trial.runs:
        item = run["item"]
        truth = set(item["ground_truth"])
        execution_error = run.get("error")
        diagnostic = None
        prediction = run.get("prediction")
        try:
            if execution_error:
                raise ValueError(str(execution_error))
            if not isinstance(prediction, dict) or not isinstance(prediction.get("ranges"), list):
                raise ValueError("Prediction must be an object with ranges array")
            found = {exact_range(value) for value in prediction["ranges"]}
        except (TypeError, ValueError) as failure:
            diagnostic = str(failure)
            found = set()
            run["evaluation_error"] = diagnostic
            run["evaluation_status"] = "penalized"
        else:
            run["evaluation_status"] = "evaluated"
        tp = len(truth & found)
        # A failed response is a false detection as well as missing all GT.
        # This penalizes failures on reviewed negative worksheets too.
        fp, fn = len(found - truth) + int(bool(diagnostic)), len(truth - found)
        errors += bool(diagnostic)
        empty += not bool(truth)
        empty_correct += not truth and not found and not diagnostic
        for i, value in enumerate((tp, fp, fn)):
            total[i] += value
            for tag in set(item.get("tags", [])):
                tags[tag][i] += value
        item_metrics = {**_scores(tp, fp, fn), "exact_match": not diagnostic and truth == found}
        run.update(dataset_item_id=item["id"], metrics=item_metrics)
        details.append({"item_id": item["id"], "input": item["input"], "tags": item.get("tags", []),
                        "ground_truth": item["ground_truth"], "prediction": prediction,
                        "error": diagnostic, "execution_status": run.get("status"),
                        "evaluation_status": run.get("evaluation_status"), **item_metrics})
    return {**_scores(*total), "items": len(details), "errors": errors,
            "reviewed_empty": empty, "correct_empty": empty_correct}, {
                "items": details, "tags": {tag: _scores(*values) for tag, values in tags.items()}}


def notebook_run(sample, input, *, execute, context, params):
    return execute({"dataset_input": input})


def jsonl_dataset(*, path, params):
    return [json.loads(line) for line in zemi_path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def csv_dataset(*, path, params):
    with zemi_path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


_BUILTINS = {"dataset": {"table_detection": table_dataset, "jsonl": jsonl_dataset, "csv": csv_dataset},
             "evaluator": {"table_detection": table_evaluator}, "run": {"notebook": notebook_run}}


def resolve_adapter(kind, name):
    if name in _BUILTINS.get(kind, {}):
        return _BUILTINS[kind][name]
    if not isinstance(name, str) or not re.fullmatch(r"@comp/[^:]+\.py:[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"{kind}.adapter: unknown built-in or invalid local callable: {name!r}")
    filename, function = name.rsplit(":", 1)
    file = zemi_path(filename)
    if not file.is_file():
        raise FileNotFoundError(f"{kind}.adapter: {file}")
    spec = importlib.util.spec_from_file_location(f"_zemi_adapter_{hashlib.sha256(str(file).encode()).hexdigest()}", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = getattr(module, function, None)
    if not callable(result):
        raise ValueError(f"{kind}.adapter: {name!r} is not callable")
    return result


class RunContext:
    """Per-sample input-only cache, owning at most one open workbook."""
    def __init__(self):
        self._path = None
        self._workbook = None

    def workbook(self, path):
        from openpyxl import load_workbook
        if self._path != path:
            self.close()
            self._workbook = load_workbook(path, data_only=True)
            self._path = path
        return self._workbook

    def close(self):
        if self._workbook is not None:
            self._workbook.close()
        self._workbook = self._path = None
