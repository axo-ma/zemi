"""Dataset optimization adapters. Ground truth stays in the host evaluator."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PureWindowsPath

from . import env


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
    """Validate the entire COCO-like split, opening at most one workbook."""
    from openpyxl import load_workbook
    source = zemi_path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("info"), dict):
        raise ValueError(f"{source}: info must be an object")
    relative_path(source.parent, data.get("annotation_policy"), "annotation_policy")
    books = _indexed(data.get("workbooks"), "workbooks")
    sheets = _indexed(data.get("worksheets"), "worksheets")
    annotations = _indexed(data.get("annotations"), "annotations")
    if not books or not sheets:
        raise ValueError("dataset must contain workbooks and worksheets")
    by_book = defaultdict(list)
    gt = defaultdict(list)
    pairs = set()
    for key, sheet in sheets.items():
        label = f"worksheets[{key}]"
        book_id = sheet.get("workbook_id")
        if book_id not in books:
            raise ValueError(f"{label}.workbook_id: unknown workbook")
        if sheet.get("status") != "reviewed":
            raise ValueError(f"{label}.status: must be reviewed (draft/blocked/unannotated are not executable)")
        name = sheet.get("name")
        if not isinstance(name, str) or not name or (book_id, name) in pairs:
            raise ValueError(f"{label}.name: invalid or duplicate worksheet")
        tags = sheet.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError(f"{label}.tags: expected strings")
        pairs.add((book_id, name))
        by_book[book_id].append((key, sheet))
    for key, annotation in annotations.items():
        sheet_id = annotation.get("worksheet_id")
        if sheet_id not in sheets or annotation.get("workbook_id") != sheets[sheet_id]["workbook_id"]:
            raise ValueError(f"annotations[{key}]: invalid workbook/worksheet reference")
        if annotation.get("status") != "reviewed":
            raise ValueError(f"annotations[{key}].status: must be reviewed")
        value = exact_range(annotation.get("range"))
        if value in gt[sheet_id]:
            raise ValueError(f"annotations[{key}]: duplicate exact range")
        gt[sheet_id].append(value)
    items = []
    seen_paths = set()
    for book_id, book in books.items():
        label = f"workbooks[{book_id}]"
        filename = relative_path(source.parent, book.get("path"), f"{label}.path")
        if filename in seen_paths or not by_book[book_id]:
            raise ValueError(f"{label}: duplicate workbook path or no worksheets")
        seen_paths.add(filename)
        expected = book.get("sha256")
        if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
            raise ValueError(f"{label}.sha256: expected lowercase SHA-256")
        with filename.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"{label}.sha256: mismatch for {filename}; expected {expected}, got {actual}")
        workbook = load_workbook(filename, read_only=True, data_only=True)
        try:
            for key, sheet in by_book[book_id]:
                if sheet["name"] not in workbook.sheetnames:
                    raise ValueError(f"worksheets[{key}].name: {sheet['name']!r} not found in {filename}")
                items.append({"id": key, "workbook_id": book_id,
                              "input": {"workbook_path": str(filename), "worksheet_name": sheet["name"]},
                              "ground_truth": gt[key], "tags": sheet.get("tags", []), "status": "reviewed"})
        finally:
            workbook.close()
    return items


def _scores(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0}


def table_evaluator(trial, *, params):
    """One-to-one exact boundaries; duplicate predictions count as FP."""
    details = []
    tags = defaultdict(lambda: [0, 0, 0])
    total = [0, 0, 0]
    errors = empty = empty_correct = 0
    for run in trial.runs:
        item = run["item"]
        truth = Counter(item["ground_truth"])
        execution_error = run.get("error")
        diagnostic = None
        prediction = run.get("prediction")
        try:
            if execution_error:
                raise ValueError(str(execution_error))
            if not isinstance(prediction, dict) or not isinstance(prediction.get("ranges"), list):
                raise ValueError("Prediction must be an object with ranges array")
            found = Counter(exact_range(value) for value in prediction["ranges"])
        except (TypeError, ValueError) as failure:
            diagnostic = str(failure)
            found = Counter()
            run["evaluation_error"] = diagnostic
            run["evaluation_status"] = "penalized"
        else:
            run["evaluation_status"] = "evaluated"
        tp = sum((truth & found).values())
        # A failed response is a false detection as well as missing all GT.
        # This penalizes failures on reviewed negative worksheets too.
        fp, fn = sum(found.values()) - tp + int(bool(diagnostic)), sum(truth.values()) - tp
        errors += bool(diagnostic)
        empty += not bool(truth)
        empty_correct += not truth and not found and not diagnostic
        for i, value in enumerate((tp, fp, fn)):
            total[i] += value
            for tag in set(item.get("tags", [])):
                tags[tag][i] += value
        details.append({"item_id": item["id"], "input": item["input"], "tags": item.get("tags", []),
                        "ground_truth": item["ground_truth"], "prediction": prediction,
                        "error": diagnostic, "execution_status": run.get("status"),
                        "evaluation_status": run.get("evaluation_status"), **_scores(tp, fp, fn)})
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
    if name in _BUILTINS[kind]:
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
