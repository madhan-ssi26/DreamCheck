# Setup and operations

Follow the [quick start](../../../README.md) from the repository root. The local checkout supplies the patched simulator and its assets. Keep `dreamcheck/`, `dream_library/`, `shared/`, and `assets/` together when distributing it. The app runs directly from the checkout.

PowerShell activation is optional. Use `.\.venv\Scripts\Activate.ps1` to make `python` point to the virtual environment, or invoke `.\.venv\Scripts\python.exe` directly. On macOS or Linux the virtual environment interpreter is `.venv/bin/python`. Rendering support outside Windows depends on the local MuJoCo/OpenGL backend.

## Live credentials

Use `DREAM_API_KEY` in the launch environment or the credentials file explained in the [YAM guide](../../../dream_library/examples/yam_mujoco/README.md). The simulator resolves the key at run time. Training and model-status commands use the bundled Dream Labs SDK. Keep credentials outside the checkout and out of shared logs. The offline reference and fixture need no API key.

## Command line

```text
python -m dreamcheck serve --port 8765
python -m dreamcheck run --reference --task orange
python -m dreamcheck run --prompt "Put the apple into the bowl" --layout stock --seconds 45
python -m dreamcheck run --retry-of RUN_ID --shorter
python -m dreamcheck run --prompt "Inspect the scene" --fixture
python -m dreamcheck train --help
```

Use `run --help` for valid options. The CLI writes a JSON result. API and simulator errors return exit code 1. Read the JSON verdict to distinguish a physical pass from a timeout.

## Local data and sharing

| Location | Data |
| --- | --- |
| `.dreamcheck/runs/` | Run JSON, final and per-response camera JPEGs, and any peak-lift images. |
| `.dreamcheck/comparisons/` | Comparison settings, paired outcomes, and links to run IDs. |
| `.dreamcheck/scenes/` | Accepted scene ZIPs and saved preview images. |
| `.dreamcheck/training/` | Demonstration bundles, manifests, and checkpoint evaluation reports. |
| Browser local storage | Recording failure labels and timestamps for the current browser origin. |

Back up `.dreamcheck/` if you need to keep local runs or training exports. Dataset uploads and live inference transmit data to Dream Labs. The recording importer processes Parquet on localhost and plays the MP4 in the browser. Review exported prompts, telemetry, frames, and uploaded demonstration footage before sharing them.

Keep one server per checkout. Use a second checkout for an independent workstation session. The HTTP server is designed for a trusted local user, with no multi-user authentication or deployment support.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Port already in use | Stop the other server or use `serve --port 8766`, then open that port in the browser. |
| Dashboard returns 403 | Open the app directly at `http://127.0.0.1:PORT/` or `http://localhost:PORT/`. |
| No key or unauthorized | Check credentials in the same account or shell that launched the process. Run the scripted reference to isolate local rendering. |
| MuJoCo cannot render | Run the reference from an interactive desktop session and check the machine's graphics drivers and MuJoCo backend. |
| Checkpoint warming or API timeout | Check the model's availability in your Dream Labs account, then retry. Completed local runs remain in History. |
| Bookmarks disappeared after changing ports | Reopen the original URL in the same browser profile. Storage is scoped to the origin. |
