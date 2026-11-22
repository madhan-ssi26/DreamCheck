"""Start the DreamCheck local app or a single run from the terminal."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .benchmark import BenchmarkManager, comparison_csv, load_evaluation_report, load_report, repair_plan
from .runner import CAMERAS, RUN_ID, RUNS_DIR, RunManager, camera_file, load_run
from .scoring import TARGETS

WEB = Path(__file__).with_name("web")
manager = RunManager()
comparisons = None


class Handler(BaseHTTPRequestHandler):
    timeout = 30

    def _local_request(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts.update({"127.0.0.1", "localhost"})
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        if (host in hosts and (origin is None or origin == f"http://{host}")
                and self.headers.get("Sec-Fetch-Site") != "cross-site"):
            return True
        self._send(json.dumps({"error": "Open DreamCheck directly on localhost."}), status=403)
        return False

    def _send(self, body, content_type="application/json", status=200, filename=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._local_request():
            return
        url = urlparse(self.path)
        path = url.path
        if path == "/api/scenes":
            from .scenes import saved_scenes

            self._send(json.dumps(saved_scenes()))
        elif path == "/api/scene-preview":
            from .scenes import scene_path

            try:
                scene = scene_path(parse_qs(url.query).get("id", [None])[0])
                self._send(scene.with_suffix(".jpg").read_bytes(), "image/jpeg")
            except (ValueError, OSError):
                self._send(b"", "image/jpeg", status=404)
        elif path == "/api/scene-example":
            from .scenes import example_package

            self._send(example_package(), "application/zip", filename="dreamcheck-workshop.zip")
        elif path == "/dreamlabs-wordmark.png":
            from .runner import ROOT

            self._send((ROOT / "assets" / "dreamlabs-logo-light.png").read_bytes(), "image/png")
        elif path == "/api/status":
            status = manager.status()
            status["preview"] = bool(status["preview"])
            status["benchmark"] = comparisons.latest()
            status["benchmark_history"] = comparisons.recent()
            self._send(json.dumps(status))
        elif path == "/api/benchmark":
            query = parse_qs(url.query)
            report_id = query.get("id", [None])[0]
            report = load_report(report_id) or load_evaluation_report(report_id)
            if not report:
                self._send(json.dumps({"error": "Comparison not found."}), status=404)
                return
            filename = f"dreamcheck-{report['id']}"
            if query.get("format") == ["csv"]:
                self._send(comparison_csv(report), "text/csv; charset=utf-8", filename=f"{filename}.csv")
            else:
                self._send(json.dumps(report, indent=2), filename=f"{filename}.json")
        elif path == "/api/repair":
            run_id = parse_qs(url.query).get("run_id", [None])[0]
            try:
                self._send(json.dumps(repair_plan(run_id)))
            except ValueError as error:
                self._send(json.dumps({"error": str(error)}), status=400)
        elif path == "/api/preview":
            view = parse_qs(url.query).get("view", ["top"])[0]
            preview = (manager.status()["preview"] or {}).get(view)
            self._send(preview or b"", "image/jpeg", 200 if preview else 204)
        elif path == "/api/run":
            run = load_run(parse_qs(url.query).get("run_id", [""])[0])
            if not run:
                self._send(json.dumps({"error": "Run not found."}), status=404)
                return
            self._send(json.dumps({"run": run, "previous": load_run(run.get("retry_of"))}))
        elif path == "/api/training":
            from .training import latest_training

            bundle_id = parse_qs(url.query).get("bundle", [None])[0]
            self._send(json.dumps(latest_training(bundle_id)))
        elif path == "/api/checkpoints":
            from .training import checkpoint_overview

            self._send(json.dumps(checkpoint_overview()))
        elif path == "/demo/relay-stage-gate.mp4":
            clip = Path(__file__).with_name("docs") / "demo" / "relay-stage-gate.mp4"
            if clip.is_file():
                self._send(clip.read_bytes(), "video/mp4")
            else:
                self._send("Not found", "text/plain", 404)
        elif path == "/api/training-status":
            from .training import cloud_status

            query = parse_qs(url.query)
            dataset_id = query.get("dataset", [None])[0]
            job_id = query.get("job", [None])[0]
            if bool(dataset_id) == bool(job_id):
                self._send(json.dumps({"error": "Choose a dataset or job ID."}), status=400)
                return
            try:
                self._send(json.dumps(cloud_status(dataset_id, job_id), default=str))
            except Exception as exc:
                self._send(json.dumps({"error": f"Dream Labs status request failed ({type(exc).__name__})."}), status=502)
        elif path == "/api/training-video":
            from .training import OUTPUT, latest_training

            query = parse_qs(url.query)
            bundle = latest_training(query.get("bundle", [None])[0])["bundle"]
            layout = query.get("layout", [""])[0]
            split = next((entry["split"] for entry in bundle["attempts"] if entry["layout"] == layout
                          and entry["split"] in {"train", "holdout"}), None) if bundle else None
            if split:
                video = OUTPUT / bundle["id"] / split / layout / "videos/chunk-000/observation.images.overhead/episode_000000.mp4"
                if video.is_file():
                    self._send(video.read_bytes(), "video/mp4")
                    return
            self._send("Video not found", "text/plain", 404)
        elif path == "/api/result-image":
            query = parse_qs(url.query)
            run_id = query.get("run_id", [""])[0]
            chunk = query.get("chunk", [""])[0]
            view = query.get("view", ["top"])[0]
            if not RUN_ID.fullmatch(run_id) or view not in CAMERAS or (chunk and (not chunk.isascii() or not chunk.isdecimal())):
                self._send("Not found", "text/plain", 404)
                return
            image_path = RUNS_DIR / run_id / camera_file(view, chunk or None, peak=query.get("peak") == ["1"])
            try:
                self._send(image_path.read_bytes(), "image/jpeg")
            except FileNotFoundError:
                self._send(b"", "image/jpeg", 204)
        elif path == "/api/policy-camera":
            query = parse_qs(url.query)
            run_id = query.get("run_id", [""])[0]
            index = query.get("index", [""])[0]
            if RUN_ID.fullmatch(run_id) and index in {"0", "1", "2"}:
                try:
                    data = (RUNS_DIR / run_id / f"policy-camera-{index}.jpg").read_bytes()
                except FileNotFoundError:
                    pass
                else:
                    self._send(data, "image/jpeg")
                    return
            self._send("Camera sample not found", "text/plain", 404)
        else:
            file = WEB / ("index.html" if path == "/" else path.lstrip("/"))
            if not file.resolve().is_relative_to(WEB.resolve()) or not file.is_file():
                self._send("Not found", "text/plain", 404)
                return
            content_type = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                            ".webp": "image/webp"}.get(file.suffix, "text/plain")
            if not content_type.startswith("image/"):
                content_type += "; charset=utf-8"
            self._send(file.read_bytes(), content_type)

    def do_POST(self):
        if not self._local_request():
            return
        path = urlparse(self.path).path
        if path == "/api/import-scene":
            import xml.etree.ElementTree as ET
            import zipfile
            from .scenes import MAX_PACKAGE, import_scene

            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= MAX_PACKAGE or self.headers.get_content_type() != "application/octet-stream":
                    raise ValueError("Select a ZIP scene package up to 48 MB.")
                self._send(json.dumps(import_scene(self.rfile.read(length))), status=201)
            except (ValueError, KeyError, TypeError, RuntimeError, OSError, AttributeError, ET.ParseError, zipfile.BadZipFile) as error:
                self._send(json.dumps({"error": f"Scene import stopped: {error}"}), status=400)
            return
        if path == "/api/recording":
            import pyarrow as pa
            from .recording import analyze_parquet

            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 32 * 1024 * 1024 or self.headers.get_content_type() != "application/octet-stream":
                    raise ValueError("Select a parquet file up to 32 MB.")
                self._send(json.dumps(analyze_parquet(self.rfile.read(length))))
            except (ValueError, pa.ArrowException, OSError, TypeError) as error:
                self._send(json.dumps({"error": str(error)}), status=400)
            return
        if path not in {"/api/run", "/api/stop", "/api/benchmark", "/api/benchmark/cancel"}:
            self._send("Not found", "text/plain", 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                raise ValueError("Invalid run request size.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Invalid run request.")
            if path == "/api/benchmark":
                self._send(json.dumps({"id": comparisons.start(data)}), status=202)
                return
            if path in {"/api/stop", "/api/benchmark/cancel"}:
                if path == "/api/stop":
                    canceled = manager.stop(data.get("id"))
                else:
                    canceled = comparisons.cancel(data.get("id"))
                self._send(json.dumps({"stopping": canceled}), status=202 if canceled else 409)
                return
            run_id = manager.start(data.get("prompt"), fixture=data.get("fixture", False),
                                   retry_of=data.get("retry_of"), actions=data.get("actions"),
                                   shorter=data.get("shorter", False), reference=data.get("reference", False),
                                   seconds=data.get("seconds"), model=data.get("model"),
                                   layout_id=data.get("layout", "stock"), task=data.get("task", "apple"),
                                   goal=data.get("goal"), assist=data.get("assist"),
                                   sim_seconds=data.get("sim_seconds"))
            self._send(json.dumps({"run_id": run_id}), status=202)
        except (ValueError, RuntimeError) as exc:
            self._send(json.dumps({"error": str(exc)}), status=409 if isinstance(exc, RuntimeError) else 400)

    def log_message(self, format, *args):
        pass


def main():
    global comparisons
    parser = argparse.ArgumentParser(description="DreamCheck workstation")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Start the local browser app")
    serve.add_argument("--port", type=int, default=8765)
    run = sub.add_parser("run", help="Start one run and print its status")
    run.add_argument("--prompt")
    run.add_argument("--layout", default="stock")
    run.add_argument("--task", choices=(*TARGETS, "relay", "sorting"), default="apple", help="Which task the judge scores")
    modes = run.add_mutually_exclusive_group()
    modes.add_argument("--fixture", action="store_true", help="Run a static scene without Dream-1 inference")
    modes.add_argument("--reference", action="store_true", help="Run a scripted physics reference")
    run.add_argument("--actions", type=int, help="Maximum executed steps per model response (1–48)")
    run.add_argument("--retry-of", help="Saved run ID to retry")
    run.add_argument("--shorter", action="store_true", help="Retry the saved prompt with half as many steps")
    run.add_argument("--seconds", type=int, help="Live task wall-clock limit (15–240 seconds)")
    run.add_argument("--sim-seconds", type=int, help="Stop after this much simulated time instead (15–240)")
    run.add_argument("--stage-gate", action="store_true", help="Relay and sorting: park the arm with no job in the current stage")
    run.add_argument("--stride", type=int, default=1, help="Execute every n-th row of each returned plan (1–3)")
    run.add_argument("--model", help="Base or DreamCheck fine-tuned checkpoint ID")
    train = sub.add_parser("train", help="Prepare demonstrations, upload, and inspect fine-tuning jobs")
    stages = train.add_subparsers(dest="stage", required=True)
    prepare = stages.add_parser("prepare", help="Generate and validate separated train and holdout datasets")
    prepare.add_argument("--task", choices=("apple", "relay", "sorting"), default="apple")
    upload = stages.add_parser("upload", help="Register the YAM body and upload the train split")
    upload.add_argument("bundle", type=Path)
    upload.add_argument("--name", required=True)
    status = stages.add_parser("status", help="Read dataset processing or training job status")
    lookup = status.add_mutually_exclusive_group()
    lookup.add_argument("--dataset")
    lookup.add_argument("--job")
    import_model = stages.add_parser("import-checkpoint", help="Attach a published checkpoint to its local dataset")
    import_model.add_argument("--job", required=True)
    submit = stages.add_parser("submit", help="Queue one paid Dream Labs training job")
    submit.add_argument("--dataset", required=True)
    submit.add_argument("--suffix", required=True)
    submit.add_argument("--steps", type=int, required=True)
    evaluate = stages.add_parser("evaluate", help="Compare the checkpoint and its base model on the task's held-out layouts")
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--repeats", type=int, default=2)
    evaluate.add_argument("--seconds", type=int, help="Per-trial limit (default 45 for apple, 90 for relay and sorting)")
    evaluate.add_argument("--task", choices=("apple", "relay", "sorting"), default="apple")
    evaluate.add_argument("--stage-gate", action="store_true", help="Relay and sorting: park the idle arm for both checkpoints and stop on simulated time")
    evaluate.add_argument("--stride", type=int, default=1, help="Execute every n-th row of the fine-tune's plans (1–3)")
    evaluate.add_argument("--simulated", action="store_true", help="Count --seconds in simulated time")
    evaluate.add_argument("--baseline", type=Path, help="Reuse a completed report from the baseline command")
    evaluate.add_argument("--resume-report", type=Path, help="Keep scored trials from a partial evaluation")
    probe = stages.add_parser("probe", help="Compare a checkpoint's plans with a scripted demonstration, state by state")
    probe.add_argument("--model", required=True)
    probe.add_argument("--task", choices=("relay", "sorting"), default="relay")
    probe.add_argument("--layout", help="Training or held-out layout (default: the first training layout)")
    probe.add_argument("--every", type=int, default=40, help="Demonstration rows between requests (10 per second)")
    baseline = stages.add_parser("baseline", help="Collect the base-model half of an evaluation")
    baseline.add_argument("--task", choices=("apple", "relay", "sorting"), default="relay")
    baseline.add_argument("--model", default="dreamlabs/dream-1-beta")
    baseline.add_argument("--repeats", type=int, default=2)
    baseline.add_argument("--seconds", type=int, default=90)
    args = parser.parse_args()
    if args.command == "train":
        from . import training

        if args.stage == "prepare":
            try:
                path, manifest = training.prepare(args.task)
            except ValueError as exc:
                parser.error(str(exc))
            print(json.dumps({"bundle": str(path), **manifest["validation"]}, indent=2))
            if not all(report["ok"] for report in manifest["validation"].values()):
                raise SystemExit(1)
        elif args.stage == "probe":
            try:
                path, report = training.probe(args.model, args.task, args.layout, args.every)
            except ValueError as exc:
                parser.error(str(exc))
            brief = [{key: value for key, value in point.items() if key not in ("expert", "predicted")}
                     for point in report["points"]]
            print(json.dumps({"report": str(path), **report, "points": brief}, indent=2))
        elif args.stage in {"evaluate", "baseline"}:
            try:
                if args.stage == "baseline":
                    path, report = training.evaluate(None, args.repeats, args.seconds, args.task, base_model=args.model)
                else:
                    seconds = args.seconds if args.seconds is not None else (45 if args.task == "apple" else 90)
                    path, report = training.evaluate(args.model, args.repeats, seconds, args.task,
                                                     baseline=args.baseline, resume=args.resume_report,
                                                     stage_gate=args.stage_gate, stride=args.stride,
                                                     simulated=args.simulated)
            except ValueError as exc:
                parser.error(str(exc))
            print(json.dumps({"report": str(path), **report}, indent=2))
            if report["status"] != "complete":
                raise SystemExit(1)
        else:
            if args.stage == "upload":
                result = training.upload(args.bundle, args.name)
            elif args.stage == "status":
                result = training.cloud_status(args.dataset, args.job)
            elif args.stage == "import-checkpoint":
                try:
                    result = training.import_checkpoint(args.job)
                except training.CheckpointError as exc:
                    parser.error(str(exc))
            else:
                result = training.queue_training(args.dataset, args.suffix, args.steps)
            print(json.dumps(result, indent=2, default=str))
        return
    if args.command == "serve":
        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535.")
        try:
            server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        except OSError as exc:
            parser.error(f"Could not start DreamCheck on port {args.port}: {exc}")
        comparisons = BenchmarkManager(manager)
        print(f"DreamCheck is available at http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()
        return
    try:
        manager.start(args.prompt, fixture=args.fixture, layout_id=args.layout, actions=args.actions,
                      retry_of=args.retry_of, shorter=args.shorter, reference=args.reference, seconds=args.seconds,
                      model=args.model, task=args.task, sim_seconds=args.sim_seconds,
                      assist={"stage_gate": args.stage_gate, "stride": args.stride})
    except ValueError as exc:
        parser.error(str(exc))
    manager.thread.join()
    print(json.dumps(manager.last_result, indent=2))
    if manager.last_result["verdict"] in {"api_error", "sim_error"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
