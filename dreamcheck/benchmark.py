"""Run reproducible Dream-1 A/B comparisons and retain the evidence."""

import csv
import io
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from statistics import mean

from .artifacts import write_json
from .layouts import HOLDOUT, RELAY_HOLDOUT, WORKSHOP
from .runner import BASE_MODEL, MODEL_ID, ROOT, RUN_ID, load_run
from .scoring import SCORED, TARGETS
from .sorting import SORT_CASES, SORT_HOLDOUT

REPORTS = ROOT / ".dreamcheck" / "comparisons"
SLOTS = ("A", "B")
SCENES = {"stock": ("stock",), "workshop": ("workshop",), "holdout": tuple(HOLDOUT), "all": ("stock", *HOLDOUT)}
PHASES = ("pickup", "transport", "release", "completed")
RELAY_PHASES = ("left_pickup", "transfer", "right_pickup", "settled", "completed")
SORTING_PHASES = (*RELAY_PHASES[:-1], "wrong_bay", "wrong_object_moved", "completed")
IMPORTED_PHASES = ("goal_region", "completed")
GATED_SIM_SECONDS = 150
FIXES = {
    "pickup": "Approach the {task} from above, center the open gripper over it, and close the gripper before lifting.",
    "transport": "Keep the {task} gripped while lifting it clear of the table and moving it directly over the center of the bowl.",
    "release": "Once the {task} is above the center of the bowl, lower it just inside the rim, fully open the gripper, then lift the empty gripper straight up.",
}
BIMANUAL_FIXES = {
    "left_pickup": "Center the open left gripper above the {task}, close it around the fruit, then lift clear of the table.",
    "transfer": "Set the {task} in the center of the flat plate. Open the left gripper and move that arm clear before the right arm approaches.",
    "right_pickup": "Wait until the {task} is resting on the plate, then reach from above with the right gripper and lift it clear.",
    "settled": "Use the right arm to lower the {task} inside the {destination}, open the gripper fully, and retract.",
    "wrong_bay": "Lower the {task} inside the named {destination} with the right arm before you open the gripper.",
    "wrong_object_moved": "Approach the {task} from directly above and keep both arms clear of the {other}.",
}


def stopping_phase(run):
    if run["verdict"] == "passed":
        return "completed"
    if run["verdict"] in {"wrong_bay", "wrong_object_moved"}:
        return run["verdict"]
    if run["verdict"] != "task_timeout":
        return None
    phase = run.get("failure_phase")
    if phase in ("plate_transfer", "placement"):
        return {"plate_transfer": "transfer", "placement": "settled"}[phase]
    if phase in (*PHASES, *RELAY_PHASES, *IMPORTED_PHASES):
        return phase
    if run.get("task") == "imported" and run.get("region_goal"):
        return "goal_region"
    checks = run.get("checks", {})
    if checks and "left_pickup" in checks:
        return next((stage for stage in RELAY_PHASES[:-1] if not checks.get(stage)), None)
    milestone = run.get("milestone", "").lower()
    if checks.get("reached_bowl") or "reached the bowl" in milestone:
        return "release"
    return "transport" if checks.get("lifted") or "lifted" in milestone else "pickup"


def repair_plan(run_id, candidate_prompt=None):
    run = load_run(run_id)
    if not run or run["mode"] != "live" or run["verdict"] not in SCORED - {"passed"}:
        raise ValueError("Choose a saved Dream-1 run with a scored task failure.")
    task = run.get("task", "apple")
    layout = run["layout_id"]
    phase = stopping_phase(run)
    if task in TARGETS and phase in FIXES:
        related = WORKSHOP if layout in WORKSHOP else HOLDOUT
        suggestion = FIXES[phase].format(task=task)
    elif task == "relay" and phase in BIMANUAL_FIXES:
        related = RELAY_HOLDOUT
        suggestion = BIMANUAL_FIXES[phase].format(task="apple", destination="bowl")
    elif task == "sorting" and layout in SORT_CASES and phase in BIMANUAL_FIXES:
        fruit, bay, _ = SORT_CASES[layout]
        related = [key for key, case in SORT_HOLDOUT.items() if case[:2] == (fruit, bay)]
        destination = "blue bay" if bay == "near_bay" else "amber bay"
        other = "orange" if fruit == "apple" else "apple"
        suggestion = BIMANUAL_FIXES[phase].format(task=fruit, destination=destination, other=other)
    elif phase == "goal_region":
        related, suggestion = (), ""
    else:
        raise ValueError("Choose a scored task failure with a recorded stage or goal region.")
    prompt = run["prompt"]
    if candidate_prompt is None:
        proposed = f"{prompt} {suggestion}"
        candidate_prompt = proposed if suggestion and suggestion not in prompt and len(proposed) <= 400 else prompt
    elif not isinstance(candidate_prompt, str) or not 1 <= len(candidate_prompt.strip()) <= 400:
        raise ValueError("Enter a candidate prompt with 1–400 characters.")
    else:
        candidate_prompt = candidate_prompt.strip()
    baseline = {"prompt": prompt, "actions": run.get("actions_per_chunk", 48), "model": run["model"], "task": task}
    if run.get("assist"):
        baseline.update(assist=run["assist"], sim_seconds=run.get("sim_seconds_limit"))
    if task == "imported":
        baseline["retry_of"] = run_id
    layouts = list(dict.fromkeys((layout, *related)))[:3]
    return {"source_run": run_id, "source_milestone": run.get("milestone"), "has_image": run.get("has_image", True),
            "phase": phase, "task": task, "instruction": suggestion,
            "seconds": run.get("seconds_limit", 60), "layouts": layouts, "scene_name": run.get("scene_name"),
            "settings": {"A": baseline, "B": {**baseline, "prompt": candidate_prompt}}}


def load_report(report_id):
    if not isinstance(report_id, str) or not RUN_ID.fullmatch(report_id):
        return None
    try:
        return json.loads((REPORTS / f"{report_id}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def save_report(report):
    REPORTS.mkdir(parents=True, exist_ok=True)
    write_json(REPORTS / f"{report['id']}.json", report)


def summarize(pairs, task="apple"):
    phases = {"sorting": SORTING_PHASES, "relay": RELAY_PHASES, "imported": IMPORTED_PHASES}.get(task, PHASES)
    matched = [pair for pair in pairs if all(pair["runs"].get(slot, {}).get("verdict") in SCORED for slot in SLOTS)]
    summary = {"matched": len(matched), "a_only": 0, "b_only": 0, "both_pass": 0, "both_miss": 0}
    for pair in matched:
        a, b = (pair["runs"][slot]["verdict"] == "passed" for slot in SLOTS)
        summary[{(True, True): "both_pass", (True, False): "a_only", (False, True): "b_only",
                 (False, False): "both_miss"}[a, b]] += 1
    for slot in SLOTS:
        runs = [pair["runs"][slot] for pair in matched]
        lifts = [run["peak_lift_mm"] for run in runs if run.get("peak_lift_mm") is not None]
        summary[slot] = {
            "passes": sum(run["verdict"] == "passed" for run in runs),
            "mean_peak_lift_mm": round(mean(lifts), 1) if lifts and len(lifts) == len(runs) else None,
            "phases": {phase: sum(stopping_phase(run) == phase for run in runs) for phase in phases},
        }
    return summary


def comparison_csv(report):
    rows = io.StringIO(newline="")
    writer = csv.writer(rows)
    heldout = report.get("source") == "evaluation"
    writer.writerow(["layout", "repeat", "setting", "verdict", "phase", "milestone", "peak_lift_mm", "clipped_steps",
                     "executed_steps", "elapsed_seconds", "run_id", *(["prompt"] if heldout else [])])
    for pair in report["pairs"]:
        for slot in SLOTS:
            run = pair["runs"].get(slot)
            if run:
                row = [pair["layout"], pair["repeat"], slot, run["verdict"], stopping_phase(run), *(run.get(key) for key in
                       ("milestone", "peak_lift_mm", "clipped_steps", "executed_steps", "elapsed_seconds", "run_id"))]
                if heldout:
                    row.append(report.get("layout_prompts", {}).get(pair["layout"], report["settings"][slot]["prompt"]))
                writer.writerow(row)
    return rows.getvalue()


def run_evidence(run):
    chunks = run["chunks"]
    tracks_lift = run.get("task") != "imported"
    peak = max(chunks, key=lambda chunk: chunk["peak_lift_mm"], default=None) if tracks_lift else None
    return {key: run.get(key) for key in ("run_id", "verdict", "milestone", "diagnosis", "elapsed_seconds", "error", "checks", "api_error")} | {
        "cameras": run.get("cameras", ["top"]),
        "has_image": run.get("has_image", True),
        "failure_phase": stopping_phase(run),
        "peak_lift_mm": peak["peak_lift_mm"] if peak else 0 if tracks_lift else None,
        "peak_chunk": peak["number"] if peak else None,
        "has_peak_image": bool(peak and peak.get("has_peak_image")),
        "clipped_steps": sum(chunk.get("clipped_steps", 0) for chunk in chunks),
        "executed_steps": sum(chunk["executed_steps"] for chunk in chunks),
    }


def load_evaluation_report(report_id):
    """Use the Compare report format for a held-out evaluation without modifying its file."""
    from .training import OUTPUT

    if not isinstance(report_id, str) or not re.fullmatch(r"evaluation-\d{8}-\d{6}-\d{6}", report_id):
        return None
    path = OUTPUT / f"{report_id}.json"
    try:
        evaluation = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    policies = {"A": "base", "B": "fine_tuned"}
    task, prompt = evaluation["task"], evaluation["prompt"]
    pairs = []
    started = {(pair["layout"], pair["repeat"]): pair["runs"] for pair in evaluation["pairs"]}
    for repeat in range(1, evaluation["repeats"] + 1):
        for layout in evaluation["layouts"]:
            runs = {}
            for slot, policy in policies.items():
                saved = started.get((layout, repeat), {}).get(policy)
                if saved is None:
                    continue
                if "peak_lift_mm" in saved:
                    runs[slot] = {**saved, "failure_phase": stopping_phase(saved)}
                else:
                    details = load_run(saved["run_id"])
                    runs[slot] = {**saved, **run_evidence(details)} if details else {
                        **saved, "has_image": False, "failure_phase": stopping_phase(saved)}
            pairs.append({"layout": layout, "repeat": repeat, "runs": runs})
    served = evaluation.get("policies", {})
    settings = {slot: {"model": evaluation["models"][policy], "prompt": prompt, "task": task,
                       "actions": served.get(policy, {}).get("actions", 24 if policy == "base" else 12),
                       "assist": served.get(policy, {}).get("assist")} for slot, policy in policies.items()}
    return {"id": report_id, "source": "evaluation", "status": evaluation["status"], "task": task,
            "variable": "checkpoint", "settings": settings, "seconds": evaluation["seconds_limit"],
            "time_basis": evaluation.get("time_basis", "wall"),
            "layout_prompts": evaluation.get("layout_prompts", {}), "order": evaluation.get("order"),
            "simulated_seconds_per_request": evaluation["simulated_seconds_per_request"],
            "pairs": pairs, "summary": summarize(pairs, task)}


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
            repair = repair_plan(options["repair_of"], options.get("candidate_prompt"))
            settings, seconds = repair["settings"], repair["seconds"]
            scope = options.get("scope", "suite")
            if scope not in ("source", "suite"):
                raise ValueError("Choose the original scene or the related scene suite.")
            gate = options.get("candidate_assist", False)
            if type(gate) is not bool or (gate and settings["A"]["task"] not in ("relay", "sorting")):
                raise ValueError("The stage gate applies to relay and sorting runs.")
            if gate:
                tuned = settings["A"]["model"].startswith("dream-1-ft:")
                seconds = GATED_SIM_SECONDS
                settings = {"A": {**settings["A"], "sim_seconds": seconds},
                            "B": {**settings["A"], "sim_seconds": seconds,
                                  "assist": {"stage_gate": True, "stride": 2 if tuned else 1}}}
            if settings["A"] == settings["B"]:
                raise ValueError("Edit the candidate prompt before starting the comparison.")
            layouts, repeats = (repair["layouts"], 3) if scope == "suite" else (repair["layouts"][:1], 1)
            variable = "assist" if gate else "prompt"
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
            "time_basis": "simulated" if settings["A"].get("sim_seconds") else "wall",
            "task": settings["A"]["task"],
            "repair": {key: repair[key] for key in ("source_run", "phase", "instruction", "scene_name")} if repair else None,
            "pairs": [{"layout": layout, "repeat": repeat, "runs": {}}
                      for repeat in range(1, repeats + 1) for layout in layouts],
        }
        report["summary"] = summarize(report["pairs"], report["task"])
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
                    report["summary"] = summarize(report["pairs"], report["task"])
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
