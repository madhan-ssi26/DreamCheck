"""Export judged YAM demonstrations and hand them to Dream Labs fine-tuning."""

import json
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .layouts import HOLDOUT, LAYOUTS, RELAY_HOLDOUT, RELAY_TRAIN, TRAIN, place_apple
from .reference import apple_to_bowl
from .relay import PROMPT as RELAY_PROMPT, RelayScorer, relay_reference, setup_relay
from .runner import ROOT, sim_imports
from .scoring import TaskScorer

OUTPUT = ROOT / ".dreamcheck" / "training"
TASK = "put apple into the bowl"
FPS = 10
BODY_NAME = "dreamcheck-yam-v1"
PARTS = {"left_arm": [0, 6], "left_gripper": [6, 7], "right_arm": [7, 13], "right_gripper": [13, 14]}
ROLES = {"top": "overhead", "left": "wrist_l", "right": "wrist_r"}
EMBODIMENT = {"state": PARTS, "action": PARTS,
              "video": {role: f"observation.images.{role}" for role in ROLES.values()}}


def latest_training():
    manifests = sorted(OUTPUT.glob("*/manifest.json"), reverse=True)
    evaluations = sorted(OUTPUT.glob("evaluation-*.json"), reverse=True)
    baselines = sorted(OUTPUT.glob("baseline-*.json"), reverse=True)
    result = {"bundle": None, "evaluation": None, "baseline": None}
    if manifests:
        path = manifests[0]
        result["bundle"] = json.loads(path.read_text(encoding="utf-8"))
        result["bundle"]["id"] = path.parent.name
    if evaluations:
        result["evaluation"] = json.loads(evaluations[0].read_text(encoding="utf-8"))
    if baselines:
        result["baseline"] = json.loads(baselines[0].read_text(encoding="utf-8"))
    return result


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
    env = YamEnv()
    try:
        relay = task == "relay"
        if relay:
            setup_relay(env, LAYOUTS[layout])
        else:
            place_apple(env, layout)
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

            trajectory = relay_reference if relay else apple_to_bowl
            for _phase, _end in trajectory(env, action_hold=2, on_action=capture):
                stats = scorer.observe()
                if stats["verdict"] == "passed":
                    break
            for video, stream in writers.values():
                video.mux(stream.encode(None))
        passed = bool(stats and stats["verdict"] == "passed")
        (root / "rollout.json").write_text(json.dumps({
            "body": BODY_NAME, "task": RELAY_PROMPT if relay else TASK, "fps": FPS, "cameras": list(ROLES.values()),
            "source": "scripted MuJoCo reference", "layout": layout, "physical_pass": passed,
        }), encoding="utf-8")
        info = rollout_episode.finish(root, status="complete" if passed else "partial", stop_reason="judge")
        if info["frames"] != count:
            raise ValueError(f"{layout}: expected {count} aligned frames, writer kept {info['frames']}.")
        return {"layout": layout, "offset_m": LAYOUTS[layout], "passed": passed, "frames": count}
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
    relay = task == "relay"
    for split, layouts in (("train", RELAY_TRAIN if relay else TRAIN), ("holdout", RELAY_HOLDOUT if relay else HOLDOUT)):
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
    manifest = {"source": "scripted MuJoCo reference", "task": RELAY_PROMPT if relay else TASK, "embodiment": EMBODIMENT,
                "sample_rate_hz": FPS, "attempts": attempts, "validation": reports}
    root.joinpath("manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if relay and sum(entry["split"] == "train" for entry in attempts) < 40:
        raise ValueError("Relay training requires at least 40 accepted demonstrations. Inspect the bundle manifest.")
    return root, manifest


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
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return dataset


def cloud_status(dataset_id=None, job_id=None):
    from dream import DreamClient, fine_tuning

    with DreamClient() as client:
        if dataset_id:
            return asdict(client.get_dataset_status(dataset_id))
        if job_id:
            return client.get_finetuning_status(job_id)
        return {"datasets": [asdict(dataset) for dataset in client.get_datasets()], "jobs": fine_tuning.list()}


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
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            temporary.replace(path)
            break
        except (OSError, ValueError):
            continue
    return job


def evaluate(model, repeats, seconds, task="apple", *, baseline=None, base_model=None):
    from .runner import RunManager

    if not 1 <= repeats <= 5 or not 15 <= seconds <= 120:
        raise ValueError("Choose 1–5 repeats per layout and a 15–120 second time limit.")
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
    layouts, prompt = (RELAY_HOLDOUT, RELAY_PROMPT) if task == "relay" else (HOLDOUT, TASK)
    protocol = {"task": task, "prompt": prompt, "layouts": list(layouts), "repeats": repeats,
                "seconds_limit": seconds, "simulated_seconds_per_request": 1.2}
    report = {"models": models, **protocol, "pairs": [], "status": "running"}
    saved_pairs = {}
    if baseline is not None:
        try:
            saved = json.loads(baseline.read_text(encoding="utf-8"))
            if (saved["status"] != "complete" or saved["models"] != {"base": models["base"]}
                    or any(saved.get(key) != value for key, value in protocol.items())):
                raise ValueError
            saved_pairs = {(pair["layout"], pair["repeat"]): pair["runs"]["base"] for pair in saved["pairs"]}
            expected = {(layout, repeat) for layout in layouts for repeat in range(1, repeats + 1)}
            if (len(saved["pairs"]) != len(expected) or saved_pairs.keys() != expected
                    or any(run["verdict"] not in {"passed", "task_timeout"} for run in saved_pairs.values())):
                raise ValueError
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise ValueError("Use a complete baseline report with the same base checkpoint, task, layouts, repeats and time limit.") from exc
        report["baseline_source"] = str(baseline.resolve())
        report["order"] = "base collected before fine-tuned runs"

    def save():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(path)

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
            for name in order:
                manager.start(prompt, model=models[name], layout_id=layout, task=task,
                              actions=12 if name == "fine_tuned" else 24, seconds=seconds)
                manager.thread.join()
                run = manager.last_result
                pair["runs"][name] = {key: run.get(key) for key in
                                      ("run_id", "verdict", "milestone", "checks", "failure_phase", "elapsed_seconds", "error")}
                print(f"{layout} repeat {repetition} {name}: {run['verdict']} ({run['run_id']})", flush=True)
                if run["verdict"] not in {"passed", "task_timeout"}:
                    report["status"] = "interrupted"
                    save()
                    return path, report
                save()
    report["status"] = "complete"
    report["passes"] = {name: sum(pair["runs"][name]["verdict"] == "passed" for pair in report["pairs"])
                        for name in models}
    save()
    return path, report
