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
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def latest_training(bundle_id=None):
    manifests = sorted(OUTPUT.glob("*/manifest.json"), reverse=True)
    evaluations = sorted(OUTPUT.glob("evaluation-*.json"), reverse=True)
    baselines = sorted(OUTPUT.glob("baseline-*.json"), reverse=True)
    result = {"bundle": None, "bundles": [], "evaluation": None, "baseline": None}
    for path in manifests:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        bundle["id"] = path.parent.name
        result["bundles"].append({key: bundle[key] for key in ("id", "task")})
        if bundle_id == bundle["id"] or bundle_id is None and result["bundle"] is None:
            result["bundle"] = bundle
    if evaluations:
        result["evaluation"] = json.loads(evaluations[0].read_text(encoding="utf-8"))
    for path in baselines:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("task") == "relay":
            result["baseline"] = report
            break
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


def evaluate(model, repeats, seconds, task="apple", *, baseline=None, base_model=None, resume=None):
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
    layouts, prompt = {"apple": (HOLDOUT, TASK), "relay": (RELAY_HOLDOUT, RELAY_PROMPT),
                       "sorting": (SORT_HOLDOUT, SORTING_NAME)}[task]
    prompts = {layout: sorting_prompt(*SORT_CASES[layout][:2]) for layout in layouts} if task == "sorting" else {}
    protocol = {"task": task, "prompt": prompt, "layouts": list(layouts), "repeats": repeats,
                "seconds_limit": seconds, "simulated_seconds_per_request": 1.2}
    if prompts:
        protocol["layout_prompts"] = prompts
    scored = {"passed", "task_timeout", "wrong_bay", "wrong_object_moved"}
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
                    or any(run["verdict"] not in scored for run in saved_pairs.values())):
                raise ValueError
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise ValueError("Use a complete baseline report with the same base checkpoint, task, layouts, repeats and time limit.") from exc
        report["baseline_source"] = str(baseline.resolve())
        report["order"] = "base collected before fine-tuned runs"

    resumed = {}
    if resume is not None:
        if not saved_pairs:
            raise ValueError("Resuming a fine-tuned evaluation requires its original baseline.")
        try:
            partial = json.loads(resume.read_text(encoding="utf-8"))
            if (partial["status"] not in {"running", "interrupted"} or partial["models"] != models
                    or Path(partial["baseline_source"]).resolve() != baseline.resolve()
                    or any(partial.get(key) != value for key, value in protocol.items())):
                raise ValueError
            for pair in partial["pairs"]:
                key = (pair["layout"], pair["repeat"])
                if key in resumed or key not in saved_pairs or pair["runs"]["base"] != saved_pairs[key]:
                    raise ValueError
                run = pair["runs"].get("fine_tuned")
                resumed[key] = run
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise ValueError("Use a partial evaluation with the same checkpoint and original baseline protocol.") from exc
        report["resumed_from"] = str(resume.resolve())
        report["prior_unscored_attempts"] = [run for run in resumed.values() if run and run["verdict"] not in scored]

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
                existing = resumed.get((layout, repetition))
                if existing and existing["verdict"] in scored:
                    pair["runs"]["fine_tuned"] = existing
                    order = ()
                    save()
                else:
                    order = ("fine_tuned",)
            elif baseline_only:
                order = ("base",)
            else:
                order = ("fine_tuned", "base") if (index + repetition) % 2 else ("base", "fine_tuned")
            for name in order:
                manager.start(prompts.get(layout, prompt), model=models[name], layout_id=layout, task=task,
                              actions=12 if name == "fine_tuned" else 24, seconds=seconds)
                manager.thread.join()
                run = manager.last_result
                pair["runs"][name] = {key: run.get(key) for key in
                                      ("run_id", "verdict", "milestone", "checks", "failure_phase", "elapsed_seconds", "error",
                                       "gripper_overshoot_tolerance")}
                print(f"{layout} repeat {repetition} {name}: {run['verdict']} ({run['run_id']})", flush=True)
                if run["verdict"] not in scored:
                    report["status"] = "interrupted"
                    save()
                    return path, report
                save()
    report["status"] = "complete"
    report["passes"] = {name: sum(pair["runs"][name]["verdict"] == "passed" for pair in report["pairs"])
                        for name in models}
    save()
    return path, report
