"""Parameterized execution and reporting for ZEMI Component playbooks."""

from __future__ import annotations

import copy
import itertools
import json
import os
import re
import statistics
import sys
import time
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from . import env
from .playbook import PLAYBOOK_OUTPUT_MIME, validate_output_params


_REPEAT_NAMES = {"seed", "repeat", "repeat_number", "repetition", "repetition_number", "trial", "trial_number", "run", "run_number"}


def _playbook_name(item: object, index: int) -> str:
    if not isinstance(item, Mapping):
        raise ValueError(f"playbooks_params[{index}] must be a table")
    name = item.get("playbook_name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"playbooks_params[{index}].playbook_name must be a non-empty string")
    return name


def _resolve_params_path(value: str | Path, root: Path) -> Path:
    params = root / "params"
    label = str(value).replace("\\", "/")
    path = root / label.removeprefix("@comp/") if label.startswith("@comp/") else Path(value)
    if not path.is_absolute():
        path = params / path
    resolved = path.resolve()
    try:
        resolved.relative_to(params.resolve())
    except ValueError:
        raise ValueError("Parameter file must be inside @comp/params") from None
    if resolved.suffix.lower() != ".toml":
        raise ValueError("Parameter file must have the .toml extension")
    if not resolved.is_file():
        raise FileNotFoundError(f"Parameter file was not found: {resolved}")
    return resolved


def _resolve_params_glob(value: str | Path, root: Path) -> list[Path]:
    params = (root / "params").resolve()
    label = str(value).replace("\\", "/")
    if label.startswith("@comp/"):
        label = label.removeprefix("@comp/")
        if not label.startswith("params/"):
            raise ValueError("Parameter glob must be inside @comp/params")
        label = label.removeprefix("params/")
    pattern = Path(label)
    if pattern.is_absolute() or ".." in pattern.parts:
        raise ValueError("Parameter glob must be relative to @comp/params")
    if pattern.suffix.lower() != ".toml":
        raise ValueError("Parameter glob must select TOML files")
    matches = [p.resolve() for p in sorted(params.glob(label), key=lambda p: p.name.casefold()) if p.is_file()]
    if not matches:
        raise FileNotFoundError(f"Parameter glob matched no files: {value}")
    return matches


def _params_candidates(root: Path, value: str | Path | Sequence[str | Path] | None) -> list[Path]:
    if value is None:
        return sorted((p.resolve() for p in (root / "params").glob("*.toml") if p.is_file()), key=lambda p: (p.name != "default_params.toml", p.name.casefold()))
    if isinstance(value, (str, Path)):
        values = [value]
    elif isinstance(value, Sequence):
        values = list(value)
        if not values:
            raise ValueError("params_file sequence must not be empty")
    else:
        raise TypeError("params_file must be a path, glob, sequence of paths/globs, or None")
    result: list[Path] = []
    for item in values:
        if not isinstance(item, (str, Path)):
            raise TypeError("Every params_file item must be a string or Path")
        result.extend(_resolve_params_glob(item, root) if any(c in str(item) for c in "*?[") else [_resolve_params_path(item, root)])
    return list(dict.fromkeys(result))


def _select_params_path(root: Path, value: str | Path | Sequence[str | Path] | None) -> Path:
    candidates = _params_candidates(root, value)
    if not candidates:
        raise FileNotFoundError("No TOML parameter files were found in @comp/params")
    if len(candidates) == 1:
        return candidates[0]
    print("Available component parameter files:")
    for index, path in enumerate(candidates, 1):
        print(f"  {index}. {path.name}")
    try:
        choice = input("Select a parameter file by number or name: ").strip()
    except EOFError:
        raise RuntimeError("Multiple parameter files were found. Set params_file explicitly for non-interactive execution.") from None
    if choice.isdecimal() and 0 <= int(choice) - 1 < len(candidates):
        return candidates[int(choice) - 1]
    for path in candidates:
        if path.name == choice:
            return path
    raise ValueError(f"Invalid parameter file selection: {choice!r}")


def _json_value(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain only finite JSON-serializable values: {error}") from error


def _expand_playbook_params(params: Mapping[str, Any], label: str) -> list[dict[str, Any]]:
    """Expand direct parameter values of the exact ``{each=[...]}`` shape."""
    literal: dict[str, Any] = {}
    axes: list[tuple[str, list[Any]]] = []
    for name, value in params.items():
        if isinstance(value, Mapping) and "each" in value:
            if set(value) != {"each"}:
                raise ValueError(f"{label}.{name}: each wrapper must contain only the 'each' key")
            choices = value["each"]
            if not isinstance(choices, list):
                raise ValueError(f"{label}.{name}.each must be an array")
            if not choices:
                raise ValueError(f"{label}.{name}.each must not be empty")
            axes.append((name, [_json_value(v, f"{label}.{name}.each") for v in choices]))
        else:
            literal[name] = _json_value(value, f"{label}.{name}")
    if not axes:
        return [literal]
    expanded = []
    for values in itertools.product(*(axis[1] for axis in axes)):
        trial = copy.deepcopy(literal)
        trial.update((axes[i][0], copy.deepcopy(value)) for i, value in enumerate(values))
        expanded.append(trial)
    return expanded


def _trial_id(name: str, config_index: int, trial_index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.casefold()).strip("-") or "playbook"
    return f"p{config_index + 1:03d}-t{trial_index + 1:04d}-{slug[:48]}"


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _summarize_trials(trials: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for trial in trials:
        params = {k: v for k, v in trial.get("input_params", {}).items() if k.casefold() not in _REPEAT_NAMES}
        key = json.dumps([trial.get("playbook_name"), params], ensure_ascii=False, sort_keys=True)
        group = groups.setdefault(key, {"playbook_name": trial.get("playbook_name"), "input_params": copy.deepcopy(params), "total": 0, "succeeded": 0, "failed": 0, "output_aggregates": {}, "_values": {}})
        group["total"] += 1
        status = trial.get("status")
        if status == "succeeded":
            group["succeeded"] += 1
            for name, value in trial.get("output_params", {}).items():
                if isinstance(value, bool) or (isinstance(value, (int, float)) and not isinstance(value, bool)):
                    group["_values"].setdefault(name, []).append(value)
        elif status == "failed":
            group["failed"] += 1
    result = []
    for group in groups.values():
        values_by_name = group.pop("_values")
        for name, values in sorted(values_by_name.items()):
            if all(isinstance(v, bool) for v in values):
                count = len(values); true_count = sum(values)
                group["output_aggregates"][name] = {"type": "boolean", "count": count, "true_count": true_count, "true_rate": true_count / count}
            else:
                numeric = [float(v) for v in values]
                group["output_aggregates"][name] = {"type": "numeric", "count": len(numeric), "min": min(numeric), "max": max(numeric), "mean": statistics.fmean(numeric), "stddev": statistics.pstdev(numeric)}
        result.append(group)
    return result


class ComponentReport:
    """Canonical JSON report and self-contained offline HTML rendering."""

    def __init__(self, component_name: str, component_root: Path, run_directory: Path, params_file: str, pipeline_params: Mapping[str, Any]) -> None:
        self.path = run_directory / "report.json"
        self.html_path = run_directory / "report.html"
        trials: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {"schema_version": 1, "component_name": component_name, "component_root": str(component_root), "params_file": params_file, "pipeline_params": copy.deepcopy(dict(pipeline_params)), "started_at": _timestamp(), "finished_at": None, "status": "running", "trials": trials, "playbooks": trials, "summary": []}

    def start_trial(self, playbook: "Playbook") -> dict[str, Any]:
        entry = {"trial_id": playbook.trial_id, "playbook_name": playbook.playbook_name, "input_params": copy.deepcopy(playbook.params), "output_params": {}, "output_notebook": playbook.output_relative.as_posix(), "output_path": playbook.output_relative.as_posix(), "started_at": _timestamp(), "finished_at": None, "duration_seconds": None, "status": "running", "error": None}
        self.data["trials"].append(entry)
        self.save()
        return entry

    def start_playbook(self, name: str, output_path: Path) -> dict[str, Any]:
        entry = {"playbook_name": name, "output_path": str(output_path), "started_at": _timestamp(), "status": "running"}
        self.data["trials"].append(entry); self.save(); return entry

    @staticmethod
    def finish_playbook(entry: dict[str, Any]) -> None:
        entry.update(status="succeeded", finished_at=_timestamp())

    @staticmethod
    def fail_playbook(entry: dict[str, Any], error: BaseException) -> None:
        entry.update(status="failed", finished_at=_timestamp(), error=_error_data(error))

    def record_failure(self, error: BaseException) -> None:
        self.data["status"] = "failed"; self.data["error"] = _error_data(error); self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["summary"] = _summarize_trials(self.data["trials"])
        json_tmp = self.path.with_name(".report.json.tmp")
        json_tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(json_tmp, self.path)
        html_tmp = self.html_path.with_name(".report.html.tmp")
        html_tmp.write_text(_report_html(self.data), encoding="utf-8")
        os.replace(html_tmp, self.html_path)


class Playbook:
    """One expanded notebook trial executable through Papermill."""

    def __init__(self, component: "ZemiComponent", config: Mapping[str, Any], *, config_index: int = 0, trial_index: int = 0, params: Mapping[str, Any] | None = None) -> None:
        self.component = component; self.config = copy.deepcopy(dict(config))
        self.playbook_name = _playbook_name(config, config_index)
        self.enabled = config.get("enabled", True)
        if not isinstance(self.enabled, bool):
            raise ValueError(f"enabled must be boolean for {self.playbook_name!r}")
        configured = config.get("playbook_params", {}) if params is None else params
        if not isinstance(configured, Mapping):
            raise ValueError(f"playbook_params must be a table for {self.playbook_name!r}")
        self.params = copy.deepcopy(dict(configured))
        relative = Path(self.playbook_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid playbook_name: {self.playbook_name!r}")
        self.source_path = component.root / relative
        if not self.source_path.is_file():
            raise FileNotFoundError(f"Playbook notebook was not found: {self.source_path}")
        self.trial_id = _trial_id(self.playbook_name, config_index, trial_index)
        self.output_relative = Path("notebooks") / f"{self.trial_id}.ipynb"
        self.output_path = component.run_directory / self.output_relative

    def run(self) -> None:
        import papermill
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic(); self._print_start(); entry = self.component.report.start_trial(self)
        try:
            papermill.execute_notebook(str(self.source_path), str(self.output_path), parameters=copy.deepcopy(self.params), cwd=str(self.component.root), progress_bar=True, log_output=False, stdout_file=sys.stdout, stderr_file=sys.stderr)
            entry["output_params"] = self._extract_output_params()
            entry["timed_cells"] = self._add_cell_timings()
        except Exception as error:
            duration = time.monotonic() - started
            if self.output_path.is_file():
                try:
                    if not entry.get("output_params"):
                        entry["output_params"] = self._extract_output_params()
                except Exception as output_error:
                    entry["output_error"] = _error_data(output_error)
            try:
                entry["timed_cells"] = self._add_cell_timings()
            except Exception as timing_error:
                entry["timing_error"] = _error_data(timing_error)
            entry["duration_seconds"] = duration
            self.component.report.fail_playbook(entry, error); self.component.report.save(); self._print_failure(error, duration)
            raise
        duration = time.monotonic() - started
        entry["duration_seconds"] = duration; self.component.report.finish_playbook(entry); self.component.report.save(); self._print_success(duration)

    def _extract_output_params(self) -> dict[str, Any]:
        if not self.output_path.is_file():
            return {}
        import nbformat
        notebook = nbformat.read(self.output_path, as_version=4); found = []
        for cell in notebook.cells:
            for output in cell.get("outputs", []):
                data = output.get("data", {})
                if PLAYBOOK_OUTPUT_MIME in data:
                    found.append(data[PLAYBOOK_OUTPUT_MIME])
        if len(found) > 1:
            raise ValueError("Notebook published output_params() more than once")
        return {} if not found else validate_output_params(found[0])

    def _print_start(self) -> None:
        line = "═" * 78
        print(f"\n{line}\nZEMI COMPONENT · PLAYBOOK START\nComponent : {self.component.name}\nPlaybook  : {self.playbook_name}\nTrial     : {self.trial_id}")
        print(f"Parameters: {self.component.params_path.relative_to(self.component.root).as_posix()}\nOutput    : {self.output_path.relative_to(self.component.root).as_posix()}\n{line}")

    def _print_success(self, duration: float) -> None:
        line = "═" * 78
        print(f"{line}\n✓ PLAYBOOK COMPLETED · {self.playbook_name} · {self.trial_id}\n  Duration: {_format_duration(duration)}\n  Output  : {self.output_path.relative_to(self.component.root).as_posix()}\n{line}")

    def _print_failure(self, error: BaseException, duration: float) -> None:
        line = "!" * 78
        print(f"{line}\n✗ PLAYBOOK FAILED · {self.playbook_name} · {self.trial_id}\n  Duration: {_format_duration(duration)}\n  Error   : {type(error).__name__}: {error}\n  Output  : {self.output_path.relative_to(self.component.root).as_posix()}\n{line}")

    def _add_cell_timings(self) -> int:
        if not self.output_path.is_file():
            return 0
        import nbformat
        notebook = nbformat.read(self.output_path, as_version=4); cells = []; count = 0
        for cell in notebook.cells:
            if "zemi-cell-timing" in cell.metadata.get("tags", []):
                continue
            cells.append(cell)
            if cell.cell_type != "code":
                continue
            try:
                duration = float(cell.metadata.get("papermill", {}).get("duration"))
            except (TypeError, ValueError):
                continue
            note = nbformat.v4.new_markdown_cell(f"> ⏱ Время выполнения ячейки: **{duration:.3f} с**")
            note.metadata["tags"] = ["zemi-cell-timing"]
            note.metadata["zemi"] = {"source_cell_id": cell.get("id"), "duration_seconds": duration}
            cells.append(note); count += 1
        notebook.cells = cells; nbformat.write(notebook, self.output_path); return count


class ZemiComponent:
    """Load component parameters and own expanded playbook trial lifecycle."""

    def __init__(self, params_file: str | Path | Sequence[str | Path] | None = None) -> None:
        self.root = env.path.comp.root; self.params_path = _select_params_path(self.root, params_file)
        with self.params_path.open("rb") as file:
            self.params = tomllib.load(file)
        self.pipeline_params = _params_table(self.params, "pipeline_params"); self.component_params = _params_table(self.params, "component_params")
        configured_name = self.component_params.get("component_name")
        if configured_name is None:
            self.name = self.root.name
        elif isinstance(configured_name, str) and configured_name:
            self.name = configured_name
        else:
            raise ValueError("component_params.component_name must be a non-empty string")
        self.stop_on_error = self.component_params.get("stop_on_error", True)
        if not isinstance(self.stop_on_error, bool):
            raise ValueError("component_params.stop_on_error must be boolean")
        self.run_directory = env.path.comp.runid
        self.report = ComponentReport(self.name, self.root, self.run_directory, self.params_path.relative_to(self.root).as_posix(), self.pipeline_params)
        configs = self.params.get("playbooks_params", [])
        if not isinstance(configs, list):
            raise ValueError("playbooks_params must be an array of tables")
        playbooks = []
        for config_index, config in enumerate(configs):
            _playbook_name(config, config_index)
            raw = config.get("playbook_params", {})
            if not isinstance(raw, Mapping):
                raise ValueError(f"playbooks_params[{config_index}].playbook_params must be a table")
            for trial_index, params in enumerate(_expand_playbook_params(raw, f"playbooks_params[{config_index}].playbook_params")):
                playbooks.append(Playbook(self, config, config_index=config_index, trial_index=trial_index, params=params))
        self.playbooks = tuple(playbooks); self._closed = False; self.report.save()

    def run(self) -> None:
        first_error: BaseException | None = None
        for playbook in self.playbooks:
            if not playbook.enabled:
                continue
            try:
                playbook.run()
            except Exception as error:
                first_error = first_error or error; self.report.record_failure(error)
                if self.stop_on_error:
                    raise
        if first_error is not None:
            raise first_error

    def close(self) -> None:
        if self._closed:
            return
        if self.report.data["status"] == "running":
            self.report.data["status"] = (
                "failed"
                if any(trial.get("status") == "failed" for trial in self.report.data["trials"])
                else "succeeded"
            )
        self.report.data["finished_at"] = _timestamp(); self.report.save(); self._closed = True


def _report_html(data: Mapping[str, Any]) -> str:
    embedded = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ZEMI job report</title><style>
:root{{--bg:#f6f7fb;--card:#fff;--ink:#172033;--muted:#667085;--line:#d9deea}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}}main{{max-width:1500px;margin:auto;padding:24px}}section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin:16px 0;overflow:auto}}h1,h2{{margin:0 0 14px}}.controls{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}}input,select{{padding:8px;border:1px solid var(--line);border-radius:6px}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}}th{{cursor:pointer}}pre{{white-space:pre-wrap;word-break:break-word}}</style></head><body><main><h1>ZEMI job report</h1>
<section id="overview"><h2>Overview</h2><div></div></section><section id="summary"><h2>Summary</h2><div></div></section>
<section id="runs"><h2>Runs</h2><div class="controls"><input id="search" placeholder="Search all values"><select id="playbook-filter"><option value="">All playbooks</option></select><select id="status-filter"><option value="">All statuses</option></select><select id="parameter-filter"><option value="">Any parameter</option></select><input id="parameter-value" placeholder="Parameter value contains"></div><div></div></section>
<section id="outputs"><h2>Outputs</h2><div></div></section><section id="errors"><h2>Errors</h2><div></div></section>
<script id="report-data" type="application/json">{embedded}</script><script>
const report=JSON.parse(document.getElementById('report-data').textContent),trials=report.trials||report.playbooks||[];const text=(tag,v)=>{{const n=document.createElement(tag);n.textContent=v;return n}},json=v=>JSON.stringify(v,null,2),scalar=v=>v===null||['string','number','boolean'].includes(typeof v);
const counts={{total:trials.length,succeeded:trials.filter(x=>x.status==='succeeded').length,failed:trials.filter(x=>x.status==='failed').length}};document.querySelector('#overview div').append(text('pre',json({{component_name:report.component_name,params_file:report.params_file,status:report.status,started_at:report.started_at,finished_at:report.finished_at,counts}})));document.querySelector('#summary div').append(text('pre',json(report.summary||[])));
const ins=[...new Set(trials.flatMap(t=>Object.keys(t.input_params||{{}}).filter(k=>scalar(t.input_params[k]))))].sort(),outs=[...new Set(trials.flatMap(t=>Object.keys(t.output_params||{{}}).filter(k=>scalar(t.output_params[k]))))].sort(),cols=['trial_id','playbook_name',...ins.map(k=>'in:'+k),...outs.map(k=>'out:'+k),'status','duration_seconds','output_notebook'];let sort='trial_id',asc=true;const val=(t,k)=>k.startsWith('in:')?(t.input_params||{{}})[k.slice(3)]:k.startsWith('out:')?(t.output_params||{{}})[k.slice(4)]:t[k];
function render(){{let q=document.getElementById('search').value.toLowerCase(),p=document.getElementById('playbook-filter').value,s=document.getElementById('status-filter').value,pk=document.getElementById('parameter-filter').value,pv=document.getElementById('parameter-value').value.toLowerCase(),rows=trials.filter(t=>(!p||t.playbook_name===p)&&(!s||t.status===s)&&(!q||json(t).toLowerCase().includes(q))&&(!pk||!pv||String(val(t,pk)??'').toLowerCase().includes(pv)));rows.sort((a,b)=>String(val(a,sort)??'').localeCompare(String(val(b,sort)??''),undefined,{{numeric:true}})*(asc?1:-1));const table=document.createElement('table'),head=document.createElement('tr');cols.forEach(k=>{{const th=text('th',k);th.onclick=()=>{{asc=sort===k?!asc:true;sort=k;render()}};head.append(th)}});table.append(head);rows.forEach(t=>{{const tr=document.createElement('tr');cols.forEach(k=>{{const td=document.createElement('td'),v=val(t,k);if(k==='output_notebook'&&v){{const a=text('a',v);a.href=v;td.append(a)}}else td.textContent=v??'';tr.append(td)}});table.append(tr)}});document.querySelector('#runs div:last-child').replaceChildren(table)}}
for(const p of [...new Set(trials.map(t=>t.playbook_name))].sort()){{const o=text('option',p);o.value=p;document.getElementById('playbook-filter').append(o)}}for(const s of [...new Set(trials.map(t=>t.status))].sort()){{const o=text('option',s);o.value=s;document.getElementById('status-filter').append(o)}}for(const k of [...ins.map(k=>'in:'+k),...outs.map(k=>'out:'+k)]){{const o=text('option',k);o.value=k;document.getElementById('parameter-filter').append(o)}}['search','playbook-filter','status-filter','parameter-filter','parameter-value'].forEach(id=>document.getElementById(id).addEventListener('input',render));render();
const nested=trials.filter(t=>Object.values(t.output_params||{{}}).some(v=>!scalar(v)));document.querySelector('#outputs div').append(text(nested.length?'pre':'p',nested.length?json(nested.map(t=>({{trial_id:t.trial_id,output_params:t.output_params}}))):'No nested outputs.'));const errors=trials.filter(t=>t.status==='failed');document.querySelector('#errors div').append(text(errors.length?'pre':'p',errors.length?json(errors.map(t=>({{trial_id:t.trial_id,error:t.error}}))):'No errors.'));
</script></main></body></html>'''


def _params_table(params: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = params.get(name, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a table")
    return copy.deepcopy(dict(value))


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f} s"
    minutes, remainder = divmod(seconds, 60); return f"{int(minutes)} min {remainder:.2f} s"


def _error_data(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)}


__all__ = ["ComponentReport", "Playbook", "ZemiComponent"]
