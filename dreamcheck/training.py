"""Export judged YAM demonstrations and hand them to Dream Labs fine-tuning."""

import json
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import write_json
from .benchmark import run_evidence
from .layouts import HOLDOUT, LAYOUTS, RELAY_HOLDOUT, RELAY_TRAIN, TRAIN, place_apple
from .reference import apple_to_bowl
from .relay import PROMPT as RELAY_PROMPT, RelayScorer, relay_reference, setup_relay
from .runner import ROOT, sim_imports
from .scoring import SCORED, TaskScorer
from .sorting import SORT_CASES, SORT_HOLDOUT, SORT_TRAIN, SORTING_NAME, SortingScorer, sorting_prompt, sorting_scene

OUTPUT = ROOT / ".dreamcheck" / "training"
TASK = "put apple into the bowl"
FPS = 10
BODY_NAME = "dreamcheck-yam-v1"
PARTS = {"left_arm": [0, 6], "left_gripper": [6, 7], "right_arm": [7, 13], "right_gripper": [13, 14]}
ROLES = {"top": "overhead", "left": "wrist_l", "right": "wrist_r"}
EMBODIMENT = {"state": PARTS, "action": PARTS,
              "video": {role: f"observation.images.{role}" for role in ROLES.values()}}


def save_manifest(path, manifest):
    write_json(path, manifest, default=str)


def latest_training(bundle_id=None):
    manifests = sorted(OUTPUT.glob("*/manifest.json"), reverse=True)
    evaluations = sorted(OUTPUT.glob("evaluation-*.json"), reverse=True)
    baselines = sorted(OUTPUT.glob("baseline-*.json"), reverse=True)
    result = {"bundle": None, "bundles": [], "evaluation": None, "evaluations": [], "baseline": None, "probe": None}
    for path in manifests:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        bundle["id"] = path.parent.name
        result["bundles"].append({key: bundle[key] for key in ("id", "task")})
        if bundle_id == bundle["id"] or bundle_id is None and result["bundle"] is None:
            result["bundle"] = bundle
    bundle = result["bundle"]
    if bundle:
        task = {TASK: "apple", RELAY_PROMPT: "relay", SORTING_NAME: "sorting"}.get(bundle["task"])
        checkpoint = bundle.get("job", {}).get("checkpoint")
        for path in evaluations:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("task") == task and (not checkpoint or report["models"].get("fine_tuned") == checkpoint):
                if result["evaluation"] is None:
                    result["evaluation"] = {**report, "id": path.stem}
                if report.get("status") == "complete":
                    aids = report.get("policies", {}).get("fine_tuned", {}).get("assist") or {}
                    result["evaluations"].append({
                        "id": path.stem, "passes": report["passes"], "trials": len(report["pairs"]),
                        "seconds": report["seconds_limit"], "time_basis": report.get("time_basis", "wall"),
                        "stage_gate": bool(aids.get("stage_gate")), "stride": aids.get("stride", 1)})
    if bundle:
        for path in sorted(OUTPUT.glob("probe-*.json"), reverse=True):
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("task") == task and report.get("model") == checkpoint:
                result["probe"] = {**report, "id": path.stem}
                break
    for path in baselines:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("task") == "relay":
            result["baseline"] = report
            break
    return result


def checkpoint_overview():
    """Summarize each published checkpoint's held-out evaluations for the Home page."""
    names = {TASK: "Apple placement", RELAY_PROMPT: "Two-arm relay", SORTING_NAME: "Two-arm sorting"}
    overview = []
    for entry in latest_training()["bundles"]:
        details = latest_training(entry["id"])
        runs = details["evaluations"]
        checkpoint = details["bundle"].get("job", {}).get("checkpoint")
        if not checkpoint or not runs:
            continue
        plain = next((run for run in runs if not run["stage_gate"] and run["stride"] == 1), None)
        aided = next((run for run in runs if run["stage_gate"] or run["stride"] > 1), None)
        probe = details["probe"]
        overview.append({"bundle": entry["id"], "task": names.get(entry["task"], entry["task"]), "checkpoint": checkpoint,
                         "plain": plain, "aided": aided,
                         "probe": probe and {key: probe[key] for key in ("layout", "idle_arm_moves", "active_arm_waits")}
                         | {"points": len(probe["points"])}})
    order = list(names.values())
    return sorted(overview, key=lambda item: order.index(item["task"]) if item["task"] in order else len(order))


class CheckpointError(ValueError):
    """A checkpoint is missing or uses an incompatible training contract."""


def checkpoint_record(model):
    from dream import DreamClient

    with DreamClient() as client:
        record = next((entry for entry in client.get_models() if entry.id == model), None)
        if not record or not record.servable:
            raise CheckpointError("This checkpoint is unavailable. Check that training published a servable model ID.")
        config = record.embodiment_config or {}
        if any(config.get(key) != value for key, value in EMBODIMENT.items()):
            raise CheckpointError("This checkpoint uses a different body. Use a joint-only DreamCheck YAM fine-tune.")
        body = next((entry for entry in client.get_embodiments() if entry.id == record.embodiment), None)
        if not body or body.name.rsplit("/", 1)[-1] != BODY_NAME:
            raise CheckpointError("Use the DreamCheck YAM embodiment registered by the training upload command.")
        return record


def record_episode(root, layout, task="apple"):
    import av
    import numpy as np
    from PIL import Image
    from robots import rollout_episode, rollouts

    sim_imports()
    from dream_control import FRAME_SIZE
    from yam_env import YamEnv

    root.mkdir()
    sorting = task == "sorting"
    target, bay, offset = SORT_CASES[layout] if sorting else ("apple", "bowl", LAYOUTS[layout])
    env = sorting_scene(offset) if sorting else YamEnv()
    try:
        relay = task == "relay"
        if sorting:
            scorer = SortingScorer(env, target, bay)
            prompt = sorting_prompt(target, bay)
        elif relay:
            setup_relay(env, LAYOUTS[layout])
            prompt = RELAY_PROMPT
        else:
            place_apple(env, layout)
            prompt = TASK
        if not sorting:
            for _ in range(30):
                state = env.state()
                env.step(state["left"], state["right"])
            scorer_type = RelayScorer if relay else TaskScorer
            scorer = scorer_type(env, env.data.body("apple").xpos[2])
        stats = None
        count = 0
        with ExitStack() as stack:
            journal = stack.enter_context((root / rollout_episode.JOURNAL_NAME).open("wb"))
            writers = {}
            for camera, role in ROLES.items():
                path = rollout_episode.video_file(root, role, partial=True)
                path.parent.mkdir(parents=True)
                video = stack.enter_context(av.open(str(path), "w", format="mp4", options={
                    "movflags": "frag_keyframe+empty_moov+default_base_moof",
                }))
                stream = video.add_stream(rollouts.VIDEO_CODEC, rate=FPS, options=rollouts.VIDEO_OPTIONS)
                stream.width, stream.height = FRAME_SIZE
                stream.pix_fmt = "yuv420p"
                writers[camera] = video, stream

            def capture(left, right):
                nonlocal count
                state = env.state()
                for camera, rgb in env.images().items():
                    video, stream = writers[camera]
                    resized = Image.fromarray(rgb).resize(FRAME_SIZE, Image.Resampling.BILINEAR)
                    frame = av.VideoFrame.from_image(resized)
                    frame.pts = count
                    video.mux(stream.encode(frame))
                np.asarray([count / FPS, 0, count, *state["left"], *state["right"], *left, *right],
                           dtype=np.float64).tofile(journal)
                count += 1

            trajectory = (relay_reference(env, on_action=capture, target=target, final_destination=bay) if sorting or relay
                          else apple_to_bowl(env, action_hold=2, on_action=capture))
            for _phase, _end in trajectory:
                stats = scorer.run_score() if sorting else scorer.observe()
                if stats["verdict"]:
                    break
            for video, stream in writers.values():
                video.mux(stream.encode(None))
        passed = bool(stats and stats["verdict"] == "passed")
        (root / "rollout.json").write_text(json.dumps({
            "body": BODY_NAME, "task": prompt, "fps": FPS, "cameras": list(ROLES.values()),
            "source": "scripted MuJoCo reference", "layout": layout, "physical_pass": passed,
        }), encoding="utf-8")
        info = rollout_episode.finish(root, status="complete" if passed else "partial", stop_reason="judge")
        if info["frames"] != count:
            raise ValueError(f"{layout}: expected {count} aligned frames, writer kept {info['frames']}.")
        result = {"layout": layout, "offset_m": offset, "passed": passed, "frames": count}
        if sorting:
            result.update(target=target, bay=bay, prompt=prompt)
        return result
    finally:
        env.close()


def prepare(task="apple"):
    from dream.datasets import validate_dataset

    OUTPUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    root = OUTPUT / stamp
    for name in ("train", "holdout", "rejected", "staging"):
        (root / name).mkdir(parents=True)
    attempts = []
    training, holdout, title = {"apple": (TRAIN, HOLDOUT, TASK), "relay": (RELAY_TRAIN, RELAY_HOLDOUT, RELAY_PROMPT),
                                "sorting": (SORT_TRAIN, SORT_HOLDOUT, SORTING_NAME)}[task]
    for split, layouts in (("train", training), ("holdout", holdout)):
        for layout in layouts:
            episode = root / "staging" / layout
            try:
                result = record_episode(episode, layout, task)
            except (RuntimeError, ValueError) as error:
                result = {"layout": layout, "passed": False, "error": str(error)}
            destination = split if result["passed"] else "rejected"
            if episode.exists():
                episode.rename(root / destination / layout)
            attempts.append({"split": destination, **result})
            print(f"{layout}: {destination} ({result.get('frames', 0)} frames)", flush=True)
    root.joinpath("staging").rmdir()
    reports = {split: asdict(validate_dataset(str(root / split), EMBODIMENT)) for split in ("train", "holdout")}
    manifest = {"source": "scripted MuJoCo reference", "task": title, "embodiment": EMBODIMENT,
                "sample_rate_hz": FPS, "attempts": attempts, "validation": reports}
    save_manifest(root / "manifest.json", manifest)
    if task == "relay" and sum(entry["split"] == "train" for entry in attempts) < 40:
        raise ValueError("Relay training requires at least 40 accepted demonstrations. Inspect the bundle manifest.")
    if task == "sorting":
        check_sorting_coverage(manifest)
    return root, manifest


def check_sorting_coverage(manifest):
    for split, layouts, minimum in (("train", SORT_TRAIN, 10), ("holdout", SORT_HOLDOUT, 2)):
        conditions = {(fruit, bay) for fruit, bay, _ in layouts.values()}
        counts = {condition: 0 for condition in conditions}
        for entry in manifest["attempts"]:
            if entry["split"] == split and entry["passed"] and entry["layout"] in layouts:
                fruit, bay, _ = layouts[entry["layout"]]
                counts[fruit, bay] += 1
        if min(counts.values()) < minimum:
            raise ValueError(f"Sorting needs {minimum} accepted {split} demos per fruit and bay. Got {counts}.")


def upload(root: Path, name):
    from dream import DreamClient
    from dream.datasets import validate_dataset

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["embodiment"] != EMBODIMENT or not all(report["ok"] for report in manifest["validation"].values()):
        raise ValueError("Prepare a validated YAM training bundle first.")
    if manifest["task"] == RELAY_PROMPT:
        counts = {split: sum(entry["split"] == split and entry["passed"] for entry in manifest["attempts"])
                  for split in ("train", "holdout")}
        if counts["train"] < 40 or counts["holdout"] < 8:
            raise ValueError("Relay upload requires 40 successful training episodes and 8 successful holdout episodes.")
    if manifest["task"] == SORTING_NAME:
        check_sorting_coverage(manifest)
    with DreamClient() as client:
        report = validate_dataset(str(root / "train"), EMBODIMENT)
        if not report.ok:
            raise ValueError(report.error)
        body = next((body for body in client.get_embodiments() if body.name.rsplit("/", 1)[-1] == BODY_NAME), None)
        if body is None:
            body = client.add_embodiment(BODY_NAME, EMBODIMENT)
        elif any(body.embodiment_config.get(key) != value for key, value in EMBODIMENT.items()):
            raise ValueError(f"The saved embodiment {body.name} has a different configuration.")
        prior = next((item for item in client.get_datasets() if item.name.rsplit("/", 1)[-1] == name), None)
        receipt = manifest.get("upload", {})
        if prior and receipt.get("id") == prior.id:
            return asdict(prior)
        dataset = asdict(client.upload_dataset(str(root / "train"), name, body.name))
        manifest["upload"] = dataset
        save_manifest(root / "manifest.json", manifest)
        return dataset


def cloud_status(dataset_id=None, job_id=None):
    from dream import DreamClient, fine_tuning

    with DreamClient() as client:
        if dataset_id:
            return asdict(client.get_dataset_status(dataset_id))
        if job_id:
            return client.get_finetuning_status(job_id)
        return {"datasets": [asdict(dataset) for dataset in client.get_datasets()], "jobs": fine_tuning.list()}


def import_checkpoint(job_id):
    job = cloud_status(job_id=job_id)
    model = job.get("fine_tuned_model")
    if job.get("status") != "succeeded" or not model:
        raise CheckpointError("Choose a succeeded job with a published checkpoint.")
    for path in sorted(OUTPUT.glob("*/manifest.json"), reverse=True):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("upload", {}).get("name") != job.get("dataset") or not job.get("dataset"):
            continue
        record = checkpoint_record(model)
        receipt = {"id": job_id, "status": job["status"], "checkpoint": record.id, "base_model": record.base_model}
        manifest["job"] = receipt
        save_manifest(path, manifest)
        return {"bundle": path.parent.name, **receipt}
    raise CheckpointError("The job's dataset does not match a local uploaded training bundle.")


def queue_training(dataset_id, suffix, steps):
    from dream import DreamClient

    if not 500 <= steps <= 10000 or steps % 100:
        raise ValueError("Choose a multiple of 100 from 500 to 10000 training steps.")
    with DreamClient() as client:
        dataset = client.get_dataset_status(dataset_id)
        if dataset.status != "processed":
            raise ValueError(f"Dataset is {dataset.status}; wait for processed before training.")
        if dataset.embodiment.rsplit("/", 1)[-1] != BODY_NAME:
            raise ValueError("Choose the processed DreamCheck YAM training dataset.")
        job = client.finetune(dataset.name, suffix, steps=steps, lr_schedule="cosine", base_share=0.5)
    for path in sorted(OUTPUT.glob("*/manifest.json"), reverse=True):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("upload", {}).get("id") != dataset_id:
                continue
            manifest["job"] = {"id": job["id"]}
            save_manifest(path, manifest)
            break
        except (OSError, ValueError):
            continue
    return job


def probe_metrics(expert, predicted, state):
    """Compare one returned plan with the demonstration commands that follow the same observation."""
    import numpy as np

    expert, predicted, state = (np.asarray(value, dtype=float) for value in (expert, predicted, state))
    rows = min(len(expert), len(predicted))
    arms = {"left": slice(0, 6), "right": slice(7, 13)}
    planned = {side: float(np.abs(predicted[:rows, part] - state[part]).max()) for side, part in arms.items()}
    shown = {side: float(np.abs(expert[:rows, part] - expert[0, part]).max()) for side, part in arms.items()}
    active = max(shown, key=shown.get)
    idle = "right" if active == "left" else "left"
    half = max(1, rows // 2)
    travelled = float(np.linalg.norm(expert[half - 1, arms[active]] - expert[0, arms[active]]))
    pace = float(np.linalg.norm(predicted[half - 1, arms[active]] - state[arms[active]])) / travelled if travelled > .05 else None
    return {"rows": rows, "active_arm": active, "expert_motion_rad": {side: round(value, 3) for side, value in shown.items()},
            "planned_motion_rad": {side: round(value, 3) for side, value in planned.items()},
            "idle_arm_moves": planned[idle] > max(.15, 3 * shown[idle]),
            "active_arm_waits": shown[active] > .3 and planned[active] < .25 * shown[active],
            "pace": None if pace is None else round(pace, 2),
            "mean_error_rad": round(float(np.abs(predicted[:rows] - expert[:rows])[:, [*range(6), *range(7, 13)]].mean()), 3)}


def probe(model, task="relay", layout=None, every=40):
    """Replay a scripted demonstration and ask the checkpoint for a fresh plan at points along it.

    Each request starts a new episode on the demonstration's own observation, so the reply is the model's plan
    for that state alone. The report compares it with the commands the demonstration issued next.
    """
    import time
    from collections import deque

    import numpy as np

    sim_imports()
    import dream_control as control
    from yam_env import YamEnv

    if task not in ("relay", "sorting") or type(every) is not int or not 10 <= every <= 120:
        raise ValueError("Probe a relay or sorting checkpoint every 10–120 demonstration rows.")
    record = checkpoint_record(model)
    layouts = {**SORT_TRAIN, **SORT_HOLDOUT} if task == "sorting" else {**RELAY_TRAIN, **RELAY_HOLDOUT}
    layout = layout or next(iter(layouts))
    if layout not in layouts:
        raise ValueError("Choose a training or held-out layout of this task.")
    target, bay, offset = SORT_CASES[layout] if task == "sorting" else ("apple", "bowl", LAYOUTS[layout])
    env = sorting_scene(offset) if task == "sorting" else YamEnv()
    policy = None
    queries, commands, phases = [], [], []
    try:
        if task == "relay":
            setup_relay(env, offset)
        prompt = sorting_prompt(target, bay) if task == "sorting" else RELAY_PROMPT
        windows = {name: deque(maxlen=control.FRAMES) for name in control.VIDEO_ROLES}
        steps = 0
        while any(len(window) < control.FRAMES for window in windows.values()):
            state = env.state()
            env.step(state["left"], state["right"])
            control.capture(env, windows, keep=steps % control.FRAME_EVERY_STEPS == 0)
            steps += 1
        policy = control.open_policy(control.policy_config(model, "", False, action_hz=FPS, embodiment=EMBODIMENT),
                                     control.settings().get("DREAM_API_KEY", ""))

        def ask():
            for _attempt in range(30):
                try:
                    policy.reset_episode()
                    control.feed(policy, env, windows, prompt, model, steps, every=0)
                    return control.joint_reply(policy.get_action())
                except Exception as error:
                    waiting = getattr(error, "reason", None) in {"pool_warming", "pool_full", "queue_timeout", "rate_limited", "max_serves"}
                    if not waiting and type(error).__name__ != "DreamProtocolError":
                        raise
                    print(f"Checkpoint not serving yet ({getattr(error, 'reason', 'connection')}). Retrying.", flush=True)
                    time.sleep(20)
            raise CheckpointError("The checkpoint did not start serving.")

        def on_action(left, right):
            if len(commands) % every == 0:
                state = env.state()
                reply = ask()
                queries.append({"row": len(commands), "state": np.r_[state["left"], state["right"]],
                                "predicted": np.column_stack((reply["q_l_chunk"], reply["q_r_chunk"]))})
                print(f"row {len(commands)}: plan received", flush=True)
            commands.append(np.r_[left, right])

        for phase, _end in relay_reference(env, on_action=on_action, target=target, final_destination=bay):
            phases.append(phase)
            control.capture(env, windows, keep=steps % control.FRAME_EVERY_STEPS == 0)
            steps += 1
    finally:
        if policy is not None:
            policy.close()
        env.close()
    commands = np.asarray(commands)
    points = []
    for query in queries:
        row = query["row"]
        expert = commands[row:row + len(query["predicted"])]
        if len(expert) < 12:
            continue
        points.append({"row": row, "seconds": round(row / FPS, 1), "phase": phases[min(2 * row, len(phases) - 1)],
                       **probe_metrics(expert, query["predicted"], query["state"]),
                       "expert": np.round(expert, 3).tolist(), "predicted": np.round(query["predicted"], 3).tolist()})
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"probe-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}.json"
    report = {"model": record.id, "task": task, "layout": layout, "prompt": prompt, "rate_hz": FPS,
              "demonstration_rows": len(commands), "points": points,
              "idle_arm_moves": sum(point["idle_arm_moves"] for point in points),
              "active_arm_waits": sum(point["active_arm_waits"] for point in points)}
    write_json(path, report)
    return path, report


def evaluate(model, repeats, seconds, task="apple", *, baseline=None, base_model=None, resume=None,
             stage_gate=False, stride=1, simulated=False):
    from .assist import validate_assist
    from .runner import MAX_SECONDS, RunManager

    if not 1 <= repeats <= 5 or not 15 <= seconds <= MAX_SECONDS:
        raise ValueError(f"Choose 1–5 repeats per layout and a 15–{MAX_SECONDS} second time limit.")
    aids = {"base": validate_assist({"stage_gate": stage_gate}, task, True),
            "fine_tuned": validate_assist({"stage_gate": stage_gate, "stride": stride}, task, True)}
    simulated = simulated or stage_gate
    rows = {"base": 48, "fine_tuned": 24} if stride > 1 else {"base": 24, "fine_tuned": 12}
    baseline_only = model is None
    if baseline_only:
        if not base_model or baseline is not None:
            raise ValueError("Choose the base checkpoint for the baseline run.")
        models = {"base": base_model}
    else:
        if not isinstance(model, str) or not model.startswith("dream-1-ft:"):
            raise ValueError("Choose a published fine-tuned checkpoint ID (dream-1-ft:…).")
        models = {"base": checkpoint_record(model).base_model, "fine_tuned": model}
    manager = RunManager()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    prefix = "baseline" if baseline_only else "evaluation"
    path = OUTPUT / f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}.json"
    layouts, prompt = {"apple": (HOLDOUT, TASK), "relay": (RELAY_HOLDOUT, RELAY_PROMPT),
                       "sorting": (SORT_HOLDOUT, SORTING_NAME)}[task]
    prompts = {layout: sorting_prompt(*SORT_CASES[layout][:2]) for layout in layouts} if task == "sorting" else {}
    protocol = {"task": task, "prompt": prompt, "layouts": list(layouts), "repeats": repeats,
                "seconds_limit": seconds, "simulated_seconds_per_request": rows["base"] / 20}
    if simulated or any(aids.values()):
        protocol.update(time_basis="simulated" if simulated else "wall",
                        policies={name: {"model": checkpoint, "actions": rows[name], "assist": aids[name]}
                                  for name, checkpoint in models.items()})
    if prompts:
        protocol["layout_prompts"] = prompts
    report = {"models": models, **protocol, "pairs": [], "status": "running"}
    expected = {(layout, repeat) for layout in layouts for repeat in range(1, repeats + 1)}
    saved_pairs = {}
    if baseline is not None:
        try:
            saved = json.loads(baseline.read_text(encoding="utf-8"))
            if (saved["status"] != "complete" or saved["models"] != {"base": models["base"]}
                    or any(saved.get(key) != value for key, value in protocol.items())):
                raise ValueError
            saved_pairs = {(pair["layout"], pair["repeat"]): pair["runs"]["base"] for pair in saved["pairs"]}
            if (len(saved["pairs"]) != len(expected) or saved_pairs.keys() != expected
                    or any(run["verdict"] not in SCORED for run in saved_pairs.values())):
                raise ValueError
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise ValueError("Use a complete baseline report with the same base checkpoint, task, layouts, repeats and time limit.") from exc
        report["baseline_source"] = str(baseline.resolve())
        report["order"] = "base collected before fine-tuned runs"

    resumed = {}
    if resume is not None:
        try:
            partial = json.loads(resume.read_text(encoding="utf-8"))
            original_baseline = Path(partial["baseline_source"]).resolve() if "baseline_source" in partial else None
            if (partial["status"] not in {"running", "interrupted"} or partial["models"] != models
                    or original_baseline != (baseline.resolve() if baseline else None)
                    or any(partial.get(key) != value for key, value in protocol.items())):
                raise ValueError
            for pair in partial["pairs"]:
                key = (pair["layout"], pair["repeat"])
                runs = pair["runs"]
                if key in resumed or key not in expected or runs.keys() - models.keys():
                    raise ValueError
                if saved_pairs and runs["base"] != saved_pairs[key]:
                    raise ValueError
                resumed[key] = runs
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise ValueError("Use a partial report with the same checkpoints, protocol, and baseline if originally supplied.") from exc
        report["resumed_from"] = str(resume.resolve())
        report["prior_unscored_attempts"] = [
            *partial.get("prior_unscored_attempts", []),
            *(run for runs in resumed.values() for run in runs.values() if run["verdict"] not in SCORED),
        ]

    def save():
        write_json(path, report)

    save()
    for repetition in range(1, repeats + 1):
        for index, layout in enumerate(layouts):
            pair = {"layout": layout, "repeat": repetition, "runs": {}}
            report["pairs"].append(pair)
            if saved_pairs:
                pair["runs"]["base"] = saved_pairs[(layout, repetition)]
                order = ("fine_tuned",)
            elif baseline_only:
                order = ("base",)
            else:
                order = ("fine_tuned", "base") if (index + repetition) % 2 else ("base", "fine_tuned")
            pair["runs"].update({name: run for name, run in resumed.get((layout, repetition), {}).items()
                                 if run["verdict"] in SCORED})
            if pair["runs"]:
                save()
            for name in order:
                if name in pair["runs"]:
                    continue
                manager.start(prompts.get(layout, prompt), model=models[name], layout_id=layout, task=task,
                              actions=rows[name], seconds=seconds, assist=aids[name],
                              sim_seconds=seconds if simulated else None)
                manager.thread.join()
                run = manager.last_result
                pair["runs"][name] = {**run_evidence(run), "failure_phase": run.get("failure_phase"),
                                      "gripper_overshoot_tolerance": run.get("gripper_overshoot_tolerance")}
                print(f"{layout} repeat {repetition} {name}: {run['verdict']} ({run['run_id']})", flush=True)
                if run["verdict"] not in SCORED:
                    report["status"] = "interrupted"
                    save()
                    return path, report
                save()
    report["status"] = "complete"
    report["passes"] = {name: sum(pair["runs"][name]["verdict"] == "passed" for pair in report["pairs"])
                        for name in models}
    save()
    return path, report
