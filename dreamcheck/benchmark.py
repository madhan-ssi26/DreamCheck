"""Run reproducible Dream-1 A/B comparisons and retain the evidence."""

import csv
import io
import json
import threading
import uuid
from datetime import datetime, timezone
from statistics import mean

from .layouts import HOLDOUT, WORKSHOP
from .runner import BASE_MODEL, MODEL_ID, ROOT, RUN_ID, load_run
from .scoring import TARGETS

REPORTS = ROOT / ".dreamcheck" / "comparisons"
SLOTS = ("A", "B")
SCORED = {"passed", "task_timeout"}
SCENES = {"stock": ("stock",), "workshop": ("workshop",), "holdout": tuple(HOLDOUT), "all": ("stock", *HOLDOUT)}
PHASES = ("pickup", "transport", "release", "completed")
FIXES = {
    "pickup": "Approach the {task} from above, center the open gripper over it, and close the gripper before lifting.",
    "transport": "Keep the {task} gripped while lifting it clear of the table and moving it directly over the center of the bowl.",
    "release": "Once the {task} is above the center of the bowl, lower it just inside the rim, fully open the gripper, then lift the empty gripper straight up.",
}


def stopping_phase(run):
    if run["verdict"] == "passed":
        return "completed"
    if run["verdict"] != "task_timeout":
        return None
    checks = run.get("checks", {})
    milestone = run.get("milestone", "").lower()
    if run.get("failure_phase") in PHASES:
        return run["failure_phase"]
    if checks.get("reached_bowl") or "reached the bowl" in milestone:
        return "release"
    return "transport" if checks.get("lifted") or "lifted" in milestone else "pickup"


def repair_plan(run_id):
    run = load_run(run_id)
    if not run or run["mode"] != "live" or run["verdict"] != "task_timeout" or run.get("task", "apple") not in TARGETS:
        raise ValueError("Choose a saved Dream-1 fruit-placement timeout to test a fix.")
    phase = stopping_phase(run)
    task = run.get("task", "apple")
    prompt = run["prompt"]
    instruction = FIXES[phase].format(task=task)
    if instruction in prompt or len(prompt) + len(instruction) + 1 > 400:
        raise ValueError("This prompt already has the suggested instruction or is too long to append it.")
    baseline = {"prompt": prompt, "actions": run.get("actions_per_chunk", 48), "model": run["model"], "task": task}
    layout = run["layout_id"]
    related = WORKSHOP if layout in WORKSHOP else HOLDOUT
    layouts = list(dict.fromkeys((layout, *related)))[:3]
    return {"source_run": run_id, "phase": phase, "task": task, "instruction": instruction,
            "seconds": run.get("seconds_limit", 60), "layouts": layouts,
            "settings": {"A": baseline, "B": {**baseline, "prompt": f"{prompt} {instruction}"}}}


def load_report(report_id):
    if not isinstance(report_id, str) or not RUN_ID.fullmatch(report_id):
        return None
    try:
        return json.loads((REPORTS / f"{report_id}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def save_report(report):
    REPORTS.mkdir(parents=True, exist_ok=True)
    temporary = REPORTS / f"{report['id']}.tmp"
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(temporary.with_suffix(".json"))


def summarize(pairs):
    matched = [pair for pair in pairs if all(pair["runs"].get(slot, {}).get("verdict") in SCORED for slot in SLOTS)]
    summary = {"matched": len(matched), "a_only": 0, "b_only": 0, "both_pass": 0, "both_miss": 0}
    for pair in matched:
        a, b = (pair["runs"][slot]["verdict"] == "passed" for slot in SLOTS)
        summary[{(True, True): "both_pass", (True, False): "a_only", (False, True): "b_only",
                 (False, False): "both_miss"}[a, b]] += 1
    for slot in SLOTS:
        runs = [pair["runs"][slot] for pair in matched]
        summary[slot] = {
            "passes": sum(run["verdict"] == "passed" for run in runs),
            "mean_peak_lift_mm": round(mean(run["peak_lift_mm"] for run in runs), 1) if runs else None,
            "phases": {phase: sum(stopping_phase(run) == phase for run in runs) for phase in PHASES},
        }
    return summary


def comparison_csv(report):
    rows = io.StringIO(newline="")
    writer = csv.writer(rows)
    writer.writerow(["layout", "repeat", "setting", "verdict", "phase", "milestone", "peak_lift_mm", "clipped_steps",
                     "executed_steps", "elapsed_seconds", "run_id"])
    for pair in report["pairs"]:
        for slot in SLOTS:
            run = pair["runs"].get(slot)
            if run:
                writer.writerow([pair["layout"], pair["repeat"], slot, run["verdict"], stopping_phase(run), *(run.get(key) for key in
                                 ("milestone", "peak_lift_mm", "clipped_steps", "executed_steps",
                                  "elapsed_seconds", "run_id"))])
    return rows.getvalue()


def run_evidence(run):
    chunks = run["chunks"]
    peak = max(chunks, key=lambda chunk: chunk["peak_lift_mm"], default=None)
    return {key: run.get(key) for key in ("run_id", "verdict", "milestone", "diagnosis", "elapsed_seconds", "error")} | {
        "cameras": run.get("cameras", ["top"]),
        "failure_phase": stopping_phase(run),
        "peak_lift_mm": peak["peak_lift_mm"] if peak else 0,
        "peak_chunk": peak["number"] if peak else None,
        "has_peak_image": bool(peak and peak.get("has_peak_image")),
        "clipped_steps": sum(chunk.get("clipped_steps", 0) for chunk in chunks),
        "executed_steps": sum(chunk["executed_steps"] for chunk in chunks),
    }


def settings_from_request(options):
    variable = options.get("variable")
    task = options.get("task", "apple")
    if task not in TARGETS:
        raise ValueError("Choose apple or orange into the bowl.")
    prompt = options.get("prompt_a")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 400:
        raise ValueError("Enter a baseline prompt with 1–400 characters.")
    settings = {slot: {"prompt": prompt.strip(), "actions": 24, "model": BASE_MODEL, "task": task} for slot in SLOTS}
    if variable == "prompt":
        candidate = options.get("prompt_b")
        if not isinstance(candidate, str) or not candidate.strip() or len(candidate.strip()) > 400:
            raise ValueError("Enter a candidate prompt with 1–400 characters.")
        settings["B"]["prompt"] = candidate.strip()
    elif variable == "cadence":
        for slot, key in zip(SLOTS, ("actions_a", "actions_b")):
            actions = options.get(key)
            if type(actions) is not int or not 1 <= actions <= 48:
                raise ValueError("Choose 1–48 steps per response for each setting.")
            settings[slot]["actions"] = actions
    elif variable == "checkpoint":
        model = options.get("model_b")
        if not isinstance(model, str) or not MODEL_ID.fullmatch(model) or model == BASE_MODEL:
            raise ValueError("Enter a published Dream Labs checkpoint ID other than Dream-1 Beta.")
        settings["B"].update(model=model, actions=12 if model.startswith("dream-1-ft:") else 24)
    else:
        raise ValueError("Choose prompt, step limit, or checkpoint as the comparison variable.")
    if settings["A"] == settings["B"]:
        raise ValueError("Choose two different settings.")
    return settings


class BenchmarkManager:
    def __init__(self, runs):
        self.runs = runs
        self.lock = threading.Lock()
        self.thread = None
        self.stop_event = None
        self.active_run = None
        paths = sorted(REPORTS.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        self.latest_id = paths[0].stem if paths else None
        previous = load_report(self.latest_id)
        if previous and previous["status"] == "running":
            previous.update(status="interrupted", active=None, error="The app closed before this comparison finished.")
            save_report(previous)

    def latest(self):
        with self.lock:
            return load_report(self.latest_id)

    def recent(self):
        paths = sorted(REPORTS.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:12]
        return [{key: report[key] for key in ("id", "status", "variable", "started_at")}
                for path in paths if (report := load_report(path.stem))]

    def start(self, options):
        repair = None
        if "repair_of" in options:
            repair = repair_plan(options["repair_of"])
            settings, seconds = repair["settings"], repair["seconds"]
            scope = options.get("scope", "suite")
            if scope not in ("source", "suite"):
                raise ValueError("Choose the original scene or the three-scene suite.")
            layouts, repeats = (repair["layouts"], 3) if scope == "suite" else (repair["layouts"][:1], 1)
            variable = "prompt"
        else:
            settings = settings_from_request(options)
            scenes = options.get("scenes", "holdout")
            repeats = options.get("repeats", 1)
            seconds = options.get("seconds", 30)
            if not isinstance(scenes, str) or scenes not in SCENES:
                raise ValueError("Choose one of the available scene sets.")
            if type(repeats) is not int or not 1 <= repeats <= 3 or type(seconds) is not int or not 15 <= seconds <= 60:
                raise ValueError("Choose 1–3 repeats and 15–60 seconds per trial.")
            layouts = SCENES[scenes]
            variable = options["variable"]
        report_id = uuid.uuid4().hex[:12]
        report = {
            "id": report_id, "started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
            "variable": variable, "settings": settings, "seconds": seconds, "active": None,
            "task": settings["A"]["task"], "repair": {key: repair[key] for key in ("source_run", "phase", "instruction")} if repair else None,
            "pairs": [{"layout": layout, "repeat": repeat, "runs": {}}
                      for repeat in range(1, repeats + 1) for layout in layouts],
        }
        report["summary"] = summarize(report["pairs"])
        with self.lock:
            self.runs.reserve(report_id)
            try:
                save_report(report)
                self.latest_id = report_id
                self.stop_event = threading.Event()
                self.thread = threading.Thread(target=self._work, args=(report, self.stop_event), daemon=True)
                self.thread.start()
            except Exception:
                self.runs.release(report_id)
                raise
        return report_id

    def cancel(self, report_id):
        with self.lock:
            if self.latest_id != report_id or not self.thread or not self.thread.is_alive():
                return False
            self.stop_event.set()
            if self.active_run:
                self.runs.stop(self.active_run)
            return True

    def _work(self, report, stop_event):
        report_id = report["id"]
        try:
            for index, pair in enumerate(report["pairs"]):
                order = SLOTS if index % 2 == 0 else SLOTS[::-1]
                for slot in order:
                    with self.lock:
                        if stop_event.is_set():
                            report["status"] = "canceled"
                            return
                        self.active_run = self.runs.start(**report["settings"][slot], layout_id=pair["layout"],
                                                          seconds=report["seconds"], owner=report_id)
                        report["active"] = {"slot": slot, "layout": pair["layout"], "repeat": pair["repeat"],
                                            "run_id": self.active_run}
                        save_report(report)
                    self.runs.thread.join()
                    run = self.runs.last_result
                    pair["runs"][slot] = run_evidence(run)
                    report["active"] = None
                    report["summary"] = summarize(report["pairs"])
                    with self.lock:
                        self.active_run = None
                        save_report(report)
                    if stop_event.is_set():
                        report["status"] = "canceled"
                        return
                    if run["verdict"] not in SCORED:
                        report.update(status="interrupted", error=run.get("error") or f"Setting {slot} could not run.")
                        return
            report["status"] = "complete"
        except Exception as error:
            report.update(status="interrupted", error=f"Comparison stopped ({type(error).__name__}).")
        finally:
            report.update(active=None, finished_at=datetime.now(timezone.utc).isoformat())
            with self.lock:
                self.active_run = None
                try:
                    save_report(report)
                finally:
                    self.runs.release(report_id)
