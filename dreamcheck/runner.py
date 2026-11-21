"""Run Dream-1 in the simulator and save the evidence used to judge the task."""

import base64
import io
import json
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from math import dist
from pathlib import Path

from .diagnostics import api_failure, summarize_chunk
from .layouts import LAYOUTS, place_target, scene_for_layout
from .relay import PROMPT as RELAY_PROMPT, RelayScorer, relay_reference, setup_relay
from .scoring import TARGETS, TaskScorer
from .scenes import SCENE_ID, ImportedScene, RegionGoal, scene_metadata, scene_path, validate_goal

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / "dream_library" / "examples" / "yam_mujoco"
RUNS_DIR = ROOT / ".dreamcheck" / "runs"
RUN_ID = re.compile(r"[0-9a-f]{12}")
DEFAULT_ACTIONS = 48
DEFAULT_SECONDS = 60
BASE_MODEL = "dreamlabs/dream-1-beta"
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:/.-]{0,159}")
CAMERAS = ("top", "overview", "left", "right")


def camera_file(view, chunk=None, peak=False):
    """Keep original overhead filenames so existing result links still work."""
    prefix = "peak" if peak else "chunk"
    if chunk is None:
        return "overhead.jpg" if view == "top" else f"{view}.jpg"
    suffix = "" if view == "top" else f"-{view}"
    return f"{prefix}-{chunk}{suffix}.jpg"


def sim_imports():
    path = str(SIM_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)


def load_run(run_id):
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        return None
    try:
        return json.loads((RUNS_DIR / run_id / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def jpeg(rgb):
    from PIL import Image

    stream = io.BytesIO()
    Image.fromarray(rgb).save(stream, format="JPEG", quality=78)
    return stream.getvalue()


def diagnose_timeout(run):
    """Describe the outcome using measured motion and contacts."""
    checks = run["checks"]
    end = run["end_state"]
    fruit = f"The {run['task']}"
    phase = "release" if checks["reached_bowl"] else "transport" if checks["lifted"] else "pickup"
    if checks["lifted"] and end["resting_outside"]:
        return phase, f"{fruit} lifted, then came to rest outside the bowl."
    if checks["reached_bowl"]:
        return phase, f"{fruit} reached the bowl without a stable release."
    if checks["lifted"]:
        suffix = " It was still in contact with an arm at the deadline." if end["arm_contact"] else ""
        return phase, f"{fruit} lifted without reaching the bowl." + suffix
    displacement = dist(run["initial_target_position"][:2], run["target_position"][:2])
    if displacement >= 0.010:
        return phase, f"{fruit} moved {displacement * 1000:.0f} mm across the scene without a pickup."
    return phase, f"The run ended before the {run['task']} was picked up."


class RunManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.current = None
        self.preview = None
        self.owner = None
        self.stop_event = None
        paths = sorted(RUNS_DIR.glob("*/result.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
        saved = [run for path in paths if (run := load_run(path.parent.name)) is not None]
        self.recent = [self.run_summary(run) for run in saved]
        self.last_result = saved[0] if saved else None
        self.previous_result = load_run(self.last_result.get("retry_of")) if self.last_result else None

    @staticmethod
    def run_summary(run):
        keys = ("run_id", "mode", "prompt", "verdict", "created_at", "actions_per_chunk", "model", "layout_id", "task", "scene_name")
        return {key: run.get(key) for key in keys}

    def status(self):
        with self.lock:
            return {"current": self.current, "last_result": self.last_result,
                    "previous_result": self.previous_result, "preview": self.preview, "recent": self.recent}

    def reserve(self, owner):
        with self.lock:
            if self.owner or self.current:
                raise RuntimeError("The simulator is already in use.")
            self.owner = owner

    def release(self, owner):
        with self.lock:
            if self.owner == owner:
                self.owner = None

    def stop(self, run_id):
        with self.lock:
            if not self.current or self.current["run_id"] != run_id:
                return False
            self.stop_event.set()
            self.current = {**self.current, "status": "stopping"}
            return True

    def start(self, prompt, fixture=False, retry_of=None, layout_id="stock", actions=None, shorter=False,
              reference=False, seconds=None, model=None, task="apple", *, owner=None, goal=None):
        if any(type(flag) is not bool for flag in (fixture, shorter, reference)) or (fixture and reference):
            raise ValueError("Choose one run mode.")
        with self.lock:
            if self.owner != owner:
                raise RuntimeError("A comparison is using the simulator.")
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError("A run is already in progress.")
            parent = load_run(retry_of)
            if retry_of is not None and parent is None:
                raise ValueError("The run to retry could not be found.")
            if reference and parent:
                raise ValueError("Start a new scripted reference run.")
            if parent:
                task = parent.get("task", "apple")
                goal = parent.get("region_goal")
            if task not in (*TARGETS, "relay") and not (parent and task == "imported"):
                raise ValueError("Choose apple, orange, or the two-arm relay.")
            if reference:
                prompt = RELAY_PROMPT if task == "relay" else f"Put {task} into the bowl (scripted reference)"
            previous_actions = parent.get("actions_per_chunk", DEFAULT_ACTIONS) if parent else DEFAULT_ACTIONS
            if shorter:
                if not parent or parent["mode"] != "live" or previous_actions <= 1:
                    raise ValueError("Choose a live run with more than one step per request.")
                prompt = parent["prompt"]
                actions = max(1, previous_actions // 2)
            elif actions is None:
                actions = previous_actions
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 400:
                raise ValueError("Enter a task prompt with 1–400 characters.")
            if type(actions) is not int or not 1 <= actions <= DEFAULT_ACTIONS:
                raise ValueError("Choose 1–48 steps per request.")
            if parent:
                layout_id = parent["layout_id"]
                fixture = parent["mode"] == "offline fixture"
                reference = parent["mode"] == "scripted reference"
                model = parent.get("model")
            if reference or fixture:
                model = None
            model = BASE_MODEL if model is None else model
            if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
                raise ValueError("Enter a Dream Labs checkpoint ID.")
            seconds_limit = parent.get("seconds_limit", DEFAULT_SECONDS) if parent else seconds
            if seconds_limit is None:
                seconds_limit = DEFAULT_SECONDS
            if type(seconds_limit) is not int or not 15 <= seconds_limit <= 120:
                raise ValueError("Choose a time limit from 15 to 120 seconds.")
            imported = isinstance(layout_id, str) and bool(SCENE_ID.fullmatch(layout_id))
            scene_name = None
            if imported:
                metadata = scene_metadata(scene_path(layout_id))
                scene_name = metadata["name"]
                goal = validate_goal(goal, metadata)
                if reference or fixture or model not in {BASE_MODEL, "dreamlabs/dream-1-beta-flash"}:
                    raise ValueError("Use Dream-1 Beta or Beta Flash for imported YAM-compatible scenes.")
            elif not isinstance(layout_id, str) or layout_id not in LAYOUTS:
                raise ValueError("Choose a supported scene layout.")
            elif goal is not None:
                raise ValueError("Custom goal regions require an imported scene.")

            run_id = uuid.uuid4().hex[:12]
            output = RUNS_DIR / run_id
            output.mkdir(parents=True)
            result = {
                "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "prompt": prompt.strip(), "layout_id": layout_id, "retry_of": retry_of,
                "task": "imported" if imported else task,
                "scene": "imported" if imported else scene_for_layout(layout_id), "cameras": CAMERAS,
                "scene_name": scene_name,
                "region_goal": goal,
                "mode": "scripted reference" if reference else "offline fixture" if fixture else "live",
                "model": "Scripted YAM trajectory" if reference else model,
                "policy_hz": 10 if model.startswith("dream-1-ft:") else 20,
                "actions_per_chunk": actions, "seconds_limit": seconds_limit,
                "verdict": None, "milestone": "Preparing simulator", "chunks": [],
            }
            self.preview = None
            self.current = {**result, "status": "starting"}
            self.stop_event = threading.Event()
            self.thread = threading.Thread(target=self._run, args=(output, result, parent, self.stop_event), daemon=True)
            self.thread.start()
            return run_id

    def _run(self, output, result, parent, stop_event):
        started = time.monotonic()
        fixture = result["mode"] == "offline fixture"
        reference = result["mode"] == "scripted reference"
        relay = result["task"] == "relay"
        target = "apple" if relay else result["task"]
        imported = target == "imported"
        observed_steps = 0
        env = None
        scorer = None
        region = None
        last_views = None
        last_position = None
        bowl_position = None
        peak_lift = 0.0
        peak_views = None
        request_inputs = {}

        def with_overview(frames):
            if "overview" not in frames:
                frames["overview"] = env.image("overview")
            return frames

        def encode(frames):
            return {view: jpeg(rgb) for view, rgb in with_overview(frames).items()}

        def store_views(frames, chunk=None, peak=False):
            for view, data in encode(frames).items():
                (output / camera_file(view, chunk, peak)).write_bytes(data)

        def publish(status, preview=None):
            with self.lock:
                self.current = {**result, "chunks": list(result["chunks"]),
                                "status": "stopping" if stop_event.is_set() else status}
                if preview is not None:
                    self.preview = preview

        def begin_scene(scene):
            nonlocal scorer, region, last_views, last_position, bowl_position
            if not imported:
                last_position = scene.data.body(target).xpos.tolist()
                bowl_position = scene.data.body("bowl").xpos.tolist()
                result["initial_target_position"] = last_position
                scorer = RelayScorer(scene, last_position[2]) if relay else TaskScorer(scene, last_position[2], target)
            elif result["region_goal"]:
                region = RegionGoal(scene, result["region_goal"])
            last_views = scene.images()
            publish("waiting" if result["mode"] == "live" else "running", encode(last_views))

        def on_wait(scene):
            if last_views is None:
                begin_scene(scene)
            else:
                publish("waiting")

        def on_step(scene, images):
            nonlocal last_views, peak_lift, peak_views, observed_steps
            observed_steps += 1
            if imported:
                result["milestone"] = "Policy actions executed in imported scene"
                if region is not None:
                    progress = region.observe()
                    result["region_progress"] = progress
                    if progress["dwell_ticks"] >= progress["required_ticks"]:
                        result.update(verdict="passed", milestone="Object stayed inside the goal region")
                    else:
                        result["milestone"] = f'Goal dwell: {progress["dwell_ticks"]}/{progress["required_ticks"]} ticks'
                last_views = dict(images)
                if observed_steps == 1 or observed_steps % 5 == 0 or result["verdict"]:
                    publish("running", encode(last_views))
                return bool(result["verdict"])
            score = scorer.observe()
            result["milestone"] = score["milestone"]
            result["target_position"] = score["target_position"]
            result["checks"] = score["checks"]
            result["end_state"] = score["end_state"]
            lift = score["target_position"][2] - scorer.initial_target_z
            if images is None and (lift > peak_lift or score["verdict"]):
                images = scene.images()
            if images is not None:
                last_views = dict(images)
                if lift > peak_lift:
                    peak_views = with_overview(last_views)
            peak_lift = max(peak_lift, lift)
            if score["verdict"]:
                result["verdict"] = score["verdict"]
            if scorer.steps == 1 or scorer.steps % 5 == 0 or score["verdict"]:
                publish("running", encode(last_views))
            return bool(score["verdict"])

        def record_segment(chunk):
            nonlocal last_position, peak_lift, peak_views
            if imported:
                number = len(result["chunks"]) + 1
                store_views(last_views, number)
                result["chunks"].append({**chunk, "number": number, "peak_lift_mm": 0,
                                         "milestone": result["milestone"], "has_image": True})
                publish("running")
                return
            position = env.data.body(target).xpos.tolist()
            number = len(result["chunks"]) + 1
            chunk.update({
                "number": number,
                "target_moved_mm": round(1000 * dist(last_position[:2], position[:2]), 1),
                "bowl_progress_mm": round(1000 * (dist(last_position[:2], bowl_position[:2])
                                                   - dist(position[:2], bowl_position[:2])), 1),
                "peak_lift_mm": round(1000 * peak_lift, 1),
                "milestone": result["milestone"],
                "has_image": last_views is not None,
                "has_peak_image": peak_views is not None,
            })
            if last_views is not None:
                store_views(last_views, number)
            if peak_views is not None:
                store_views(peak_views, number, peak=True)
            result["chunks"].append(chunk)
            last_position = position
            peak_lift = 0.0
            peak_views = None
            publish("running")

        def on_chunk(reply, applied, executed, rtt):
            record_segment(summarize_chunk(reply, applied, executed, rtt))

        def on_request(windows):
            from PIL import Image

            inputs = dict(request_inputs)
            if tuple(windows) != tuple(camera["key"] for camera in inputs.get("cameras", [])):
                cameras = []
                for index, (key, window) in enumerate(windows.items()):
                    sample = base64.b64decode(window[-1], validate=True)
                    with Image.open(io.BytesIO(sample)) as image:
                        width, height = image.size
                    (output / f"policy-camera-{index}.jpg").write_bytes(sample)
                    cameras.append({"key": key, "frames": len(window), "width": width, "height": height})
                inputs["cameras"] = cameras
                inputs["snapshot_attempt"] = inputs.get("attempts", 0) + 1
            inputs.setdefault("first_request_after_ms", round((time.monotonic() - started) * 1000))
            inputs["attempts"] = inputs.get("attempts", 0) + 1
            request_inputs.update(inputs)
            result["request_inputs"] = inputs
            publish("waiting")

        def on_reply(rtt):
            inputs = dict(request_inputs)
            inputs.setdefault("first_reply_ms", round((time.monotonic() - started) * 1000))
            inputs.setdefault("first_successful_request_rtt_ms", round(rtt * 1000))
            inputs["replies"] = inputs.get("replies", 0) + 1
            request_inputs.update(inputs)
            result["request_inputs"] = inputs

        try:
            sim_imports()
            from dream_control import main as run_task, settings
            from yam_env import YamEnv

            if result["mode"] == "live" and not settings().get("DREAM_API_KEY"):
                result["verdict"] = "api_error"
                result["error"] = "No Dream API key found. Set DREAM_API_KEY or configure ~/.dreamlabs/credentials."
                return
            if imported:
                env = ImportedScene(scene_path(result["layout_id"]).read_bytes())
            else:
                env = YamEnv(result["scene"])
                if relay:
                    setup_relay(env, LAYOUTS[result["layout_id"]])
                else:
                    place_target(env, result["layout_id"], target)
            if fixture or reference:
                for _ in range(30 if reference else 8):
                    state = env.state()
                    env.step(state["left"], state["right"])
                begin_scene(env)
                if reference:
                    from .reference import fruit_to_bowl

                    phase_steps = 0
                    trajectory = relay_reference(env) if relay else fruit_to_bowl(env, target)
                    for phase, last_step in trajectory:
                        if stop_event.is_set():
                            break
                        tick = time.monotonic()
                        phase_steps += 1
                        needs_image = last_step or (scorer.steps + 1) % 5 == 0
                        done = on_step(env, env.images() if needs_image else None)
                        if last_step or done:
                            record_segment({"phase": phase, "executed_steps": phase_steps})
                            phase_steps = 0
                        if done:
                            break
                        time.sleep(max(0, 0.05 - (time.monotonic() - tick)))
                else:
                    for _ in range(80):
                        if stop_event.is_set():
                            break
                        state = env.state()
                        env.step(state["left"], state["right"])
                        if on_step(env, env.images()):
                            break
                        time.sleep(0.05)
                    result["fixture_note"] = "Offline scene fixture. No Dream-1 requests were made."
            else:
                embodiment = None
                if result["policy_hz"] == 10:
                    from .training import EMBODIMENT, checkpoint_record

                    checkpoint_record(result["model"])
                    embodiment = EMBODIMENT
                run_task(["--task", result["prompt"], "--model", result["model"],
                          "--seconds", str(result["seconds_limit"]), "--actions", str(result["actions_per_chunk"]),
                          "--policy-hz", str(result["policy_hz"]), "--headless", "--quiet"],
                         shared_env=env, on_step=on_step, on_wait=on_wait, on_chunk=on_chunk, embodiment=embodiment,
                         stop_event=stop_event, on_request=on_request, on_reply=on_reply)
                if not stop_event.is_set() and not observed_steps:
                    result.update(verdict="api_error", error="No model actions executed before the deadline.")
            completed = "unscored" if imported and region is None else "task_timeout"
            result["verdict"] = result["verdict"] or ("canceled" if stop_event.is_set() else completed)
            if result["verdict"] == "task_timeout" and result["mode"] == "live" and "checks" in result:
                if relay:
                    result["failure_phase"] = next((stage for stage in ("left_pickup", "transfer", "right_pickup", "settled")
                                                    if not result["checks"][stage]), None)
                else:
                    result["failure_phase"], result["diagnosis"] = diagnose_timeout(result)
        except (Exception, SystemExit) as exc:
            name = type(exc).__name__
            result["verdict"] = "api_error" if name.startswith("Dream") or isinstance(exc, SystemExit) else "sim_error"
            if name == "CheckpointError":
                result.update(verdict="api_error", error=str(exc))
            elif result["verdict"] == "api_error":
                result["error"], result["api_error"] = api_failure(exc)
            else:
                result["error"] = f"Run stopped ({name}). Check the simulator or Dream-1 connection."
        finally:
            result["elapsed_seconds"] = round(time.monotonic() - started, 2)
            try:
                if last_views is not None:
                    store_views(last_views)
                    result["has_image"] = True
                temporary = output / "result.tmp"
                temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
                temporary.replace(output / "result.json")
            except OSError:
                result["error"] = "Could not save this run to disk."
            finally:
                try:
                    if env is not None:
                        env.close()
                finally:
                    with self.lock:
                        self.last_result = result
                        self.previous_result = parent
                        self.recent = [self.run_summary(result), *self.recent[:29]]
                        self.current = None
