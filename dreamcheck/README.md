# DreamCheck developer guide

Start with the [project README](../README.md) for installation and the browser walkthrough. Each component guide describes where to start, what it measures, and where its outputs go.

| Component | Implementation | Guide |
| --- | --- | --- |
| Local HTTP and CLI | `__main__.py` | [Setup and operations](docs/setup/README.md) |
| Simulator runs and evidence | `runner.py`, `layouts.py`, `scoring.py`, `reference.py`, `diagnostics.py` | [Judge and Run History](docs/judge/README.md) |
| Matched A/B runs | `benchmark.py` | [Compare](docs/compare/README.md) |
| Portable MJCF | `scenes.py` | [Scene import](docs/scenes/README.md) |
| Recorded episodes | `recording.py` | [Recording import](docs/recordings/README.md) |
| Collection and checkpoint evaluation | `training.py` | [Fine-tuning](docs/training/README.md) |
| Browser interface | `web/` | [Frontend](web/README.md) |

## Runtime

The browser polls `GET /api/status` for the run, recent history, and latest comparison. Starting a run returns a run ID while a worker owns the simulator. The runner loads the selected layout, attaches callbacks to the [YAM control loop](../dream_library/examples/yam_mujoco/dream_control.py), and saves observed state, camera frames, and command deltas. Compare reserves the simulator while it schedules its paired runs. The browser reads completed artifacts by ID. Restarting the process drops in-progress work and reloads completed artifacts from disk.

The local endpoints are implementation details. The main read endpoints are `/api/run?run_id=ID`, `/api/benchmark?id=ID`, and `/api/training`. Mutation endpoints accept JSON for runs and comparisons and binary bodies for scene and recording imports. See the component guides for supported inputs.

## Verify changes

From the repository root with the virtual environment active:

```text
python -m pip check
python -m unittest discover -s dreamcheck -t . -v
python -m dreamcheck run --reference
```

The reference result should contain `"verdict": "passed"` and `"has_image": true`. The [offline CI workflow](../.github/workflows/dreamcheck.yml) runs unit tests on Windows and Linux and checks the reference with software rendering on Linux. It uses no account credentials. Before releasing UI changes, also open Home, run the offline reference, reopen it from History, and test import with the downloadable Workshop ZIP and a local v2.1 recording. Check the first-use flow at desktop and phone widths.
