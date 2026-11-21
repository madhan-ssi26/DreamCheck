# DreamCheck prototype plan

This is the original hackathon plan. For the current app and supported workflows, see the [DreamCheck README](README.md).

## What we are building

DreamCheck helps a Dream-1 developer investigate a failed task, change the prompt, and retry from the same starting setup. Once a change works there, the developer can check whether it still works across a small set of layouts. The target user already runs Dream-1 and currently has to watch runs and reset the simulator by hand to compare them.

The first prototype supports **apple into bowl** on the stock YAM MuJoCo scene. Prompt wording can change, while the goal stays fixed. Start with `put apple into the bowl`. Keep the stock checkpoint and action settings fixed during prompt comparisons. The tool must explain which physical milestone it observed and distinguish a task miss from an infrastructure error. Expect the hosted policy to produce different actions on separate attempts, even from an identical start.

**First usable loop:** Run a prompt → see the camera and verdict → edit the prompt → retry this setup and compare. The release for Sunday adds checks across five layouts and one observed usability session with another Dream-1 user.

## Build order

| Milestone | Time budget | Exit criterion |
| --- | --- | --- |
| 0. Bring up the stock simulator | First 30–90 min | Rendering and a live Dream-1 action both work on the demo laptop. |
| 1. Build the scoring and single-run runner | About 2 hours | One command runs, scores, saves, and retries a real task from the same setup. |
| 2. Ship the first usable screen | About 1–2 more hours | A user can run, read the outcome, and retry without touching the terminal again. Target 3–5 focused hours for milestones 0–2. |
| 3. Compare layouts and prepare the demo | Remainder of an 8–12 hour total budget | A second user tries the flow, five-layout comparisons save real results, and the live demo fits in three minutes. |

### 0. Bring up the stock simulator

From the repo root on Windows with Python 3.11 or 3.12, start with the checkout and its documented dependency path. Configure credentials using the existing Dream Labs workflow or the `DREAM_API_KEY` environment variable in the current shell. Keep the key out of commands that are logged, project files, and browser inputs.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\dream_library
Push-Location .\dream_library\examples\yam_mujoco
..\..\..\.venv\Scripts\python.exe dream_control.py --dry-run --headless
..\..\..\.venv\Scripts\python.exe dream_control.py --headless --task "put apple into the bowl" --seconds 30 --quiet
Pop-Location
```

The dry run needs OpenGL because it renders the three cameras. The live run should return and execute at least one action chunk. A grasp or successful placement is useful evidence, but it is unnecessary for this setup check. Time the first response and note package, OpenGL, authentication, or service errors before changing the simulator. If credentials or the service are unavailable, continue with local scorer and UI work using clearly labeled fixture runs, and record that live inference is still pending.

### 1. Build the scoring and single-run runner

Put the prototype in a top-level `dreamcheck/` Python package. Expose `python -m dreamcheck run --prompt "put apple into the bowl" --layout stock` from the repo root. Keep `scoring.py` independent of UI code and use one runner for the CLI and the UI. The reference sim uses sibling imports, so put its directory on the import path at the adapter in one place. Keep any changes in the simulator small and backwards compatible.

- **Reuse the control loop.** Extend `dream_library/examples/yam_mujoco/dream_control.py::main` with an optional step callback, or a comparably small seam, that receives the environment and already rendered images after a policy step and can signal completion. Keep the existing `validate_chunk`, observation windows, checkpoint, `DreamClient` session handling, and default behavior for upstream callers. The stock `--seconds` deadline stops physics, but an in-flight request can delay return. A pending request must never block the UI.
- **Reset before inference.** Create and close one `YamEnv` per run on the thread that owns rendering. Apply the selected layout after `YamEnv.reset()` and before the observation window fills. For a varied layout, set only the XY translation of `env.data.joint("apple_free").qpos`, zero that joint's velocities, and call `mujoco.mj_forward`. Save the layout ID, actual starting apple pose, and relevant version metadata. Use named fixed offsets and validate that each apple starts on the table, away from other objects, and settles correctly. Start with `stock` and one shifted case. Retry reuses the recorded layout and settings with the edited prompt and a fresh policy session.
- **Score the scene at each policy step.** Use apple and bowl body positions, apple velocity, and contact pairs mapped to body IDs. Track `lifted` and `above_bowl` as milestones. A pass requires the apple to be in the bowl's interior, supported by the bowl, free of robot contact, and nearly stationary for a short continuous interval. Start with 0.5 simulated seconds (10 control steps) and calibrate containment and speed thresholds against the stock geometry. A held apple above the bowl, a transient bounce, and an apple resting outside the bowl must all fail this check. Measure lift from the settled starting height. Label an observation such as “Lifted, then returned to the table” only when the trace supports it. Otherwise show the last milestone without guessing why the policy failed.
- **Store one result per attempt.** Use a generated run ID, a local `./.dreamcheck/runs/` directory ignored by Git, a `result.json`, and an overhead JPEG from the verdict step or last recorded milestone. Save `prompt`, model and run settings, `layout_id`, initial pose, `retry_of`, verdict, last milestone, elapsed wall time, request count if exposed, and a short sanitized error when applicable. Expose `passed`, `task_timeout`, `api_error`, `sim_error`, and `canceled` when cancellation is wired. Finalize the result once. If the wall-clock budget expires before the first valid policy action while waiting on inference, record `api_error` with a service timeout reason. Keep infrastructure errors and cancellations out of the task success denominator. A verified success ends the run early.
- **Keep one motion job active.** Use the existing stop event and keep the worker busy until the policy session closes. Discard actions returned after the deadline or cancellation. The stock policy timeout is 45 seconds, so measure cold and warm response times and give DreamCheck its own configurable request timeout if needed. Keep the status visible while the request winds down. Preserve the stock timeout for normal simulator users.

Before moving to the screen, exercise the scorer with a settled apple inside the bowl, an apple outside, an apple held over the bowl, and a brief contact that fails the settle window. Use local fixtures for these cases and label them as such. Run a real prompt if credentials are available. Confirm a second attempt starts from the saved pose, verdicts survive reopening, and a bad credential produces an infrastructure result. Record which checks actually ran.

### 2. Ship the first usable screen

Use a local Python server bound to `127.0.0.1`, with one static HTML/CSS/JavaScript screen and a worker thread that owns the simulator. Flask is sufficient. `python -m dreamcheck serve` should be the single startup command after setup. Poll status and the current JPEG at a modest rate. Reuse the simulator's existing `top` image and limit preview updates to a few frames a second. Start with a camera image, prompt field, **Run**, result card, and **Retry this setup**. Retain the previous result next to the retry, including the previous prompt. Put the next action beside the diagnosis.

The first screen needs these states: ready, starting, running, waiting on Dream-1, passed, timed out, and setup/API error. Keep the UI responsive, reject duplicate run requests while busy, explain how to fix missing credentials, and ensure a reload shows the run in progress or the last saved result. Add a short quickstart once the CLI and screen launch commands work. Aim for a new user with a working simulator and configured key to start their first trial within 60 seconds of opening the app.

### 3. Compare layouts and prepare the demo

- Add **Check other layouts** after the core loop works. Compare the two prompts one layout at a time, using the same saved layout table for each. Target five matched layouts including stock. Start with three if time is tight. Show counts such as `3 passed / 5 completed`, enumerate infrastructure errors separately, and let the user rerun a specific failure. Keep the run history. The small sample demonstrates the workflow and does not establish a general improvement.
- Ask a Dream-1 mentor or another team how they currently investigate failed runs and whether their existing tool already offers this workflow. Then watch one person try their own prompt wording for the supported task. Record where they hesitate, whether they can interpret an outcome and retry without coaching, and whether they choose to run another comparison. Fix the biggest point of friction before adding polish.
- Rehearse three full demos. Start a real run within 20 seconds. While it runs, show an actual failure and the matched results collected earlier. End on the live verdict and what to change next. Choose the stage deadline from measured runs. An API issue should show up explicitly in the app, and earlier results should be presented as earlier results. Budget no more than three minutes for the full sequence.

## Reference points in the repo

| File | Reuse |
| --- | --- |
| [Simulator README](dream_library/examples/yam_mujoco/README.md) | Supported setup, credential order, stock task prompts and controls. |
| [dream_control.py](dream_library/examples/yam_mujoco/dream_control.py) | `main`, `capture`, `validate_chunk`, `open_policy`, `settings`, `BETA_MODEL`. |
| [yam_env.py](dream_library/examples/yam_mujoco/yam_env.py) | `YamEnv.reset`, `step`, `images`, `close`, `add_bowl`, `apple_free`. One control step advances 50 ms of simulation. |
| [mock_pickups.py](dream_library/examples/yam_mujoco/mock_pickups.py) | Local physics reference for measuring apple pose and lift without inference. |
| [test_env.py](dream_library/examples/yam_mujoco/test_env.py) | Existing patterns for local contact cases, mock policy responses, and cancellation. |

## Scope for the initial handoff

Work through milestones 0–2. Defer multi-task support, hardware integration, fine-tuning, cloud deployment, accounts, and full video replay. Add the layout comparison after the single-run workflow is verified. If setup or service access blocks a live milestone, report the exact blocker, continue with offline work, and keep fixture results visibly separate from live runs.
