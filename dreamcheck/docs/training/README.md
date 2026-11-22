# Fine-tune and evaluate

Training is optional. The browser workspace lets you review demonstrations, check dataset or job status, and load a published checkpoint. Use the CLI for dataset preparation, upload, and job submission.

Open **Fine-tune** and choose a dataset to see its task, validation counts, and matching evaluation. For a locally recorded job, the job ID is filled in automatically. **Check job status** shows the reported step count when available. **Held-out relay baseline** shows valid trials and the number of timeouts at each first incomplete stage. Select **Inspect example** to open evidence for that stage.

Select **Open evaluation in Compare** to inspect paired frames, find the stage where each policy stopped, and export CSV or JSON. A running report shows completed pairs so far. Refresh the dataset to pick up the latest trials or a resumed report.

1. Run `python -m dreamcheck train prepare`. The scripted YAM trajectory captures joint state, actions, and three policy cameras at 10 Hz. The physical judge admits successful placements and the SDK validates both LeRobot v2.1 splits. Six layouts are used for training and three for holdout. Read the exported `manifest.json` for actual acceptance and validation results.
2. Run `python -m dreamcheck train upload BUNDLE_PATH --name DATASET_NAME`. This registers or reuses the joint-only DreamCheck YAM embodiment and uploads the training split. Keep the holdout local. Poll with `python -m dreamcheck train status --dataset DATASET_ID` until it reports `processed`.
3. Once you have verified credits, run `python -m dreamcheck train submit --dataset DATASET_ID --suffix dreamcheck-apple-v1 --steps 500`. This schedules a paid job. Poll with `python -m dreamcheck train status --job JOB_ID`. Save the dataset ID, job ID, and eventual published checkpoint ID together.
4. Run `python -m dreamcheck train import-checkpoint --job JOB_ID` after the job succeeds. This verifies the published model's YAM contract and saves its ID with the matching local dataset. Refresh **Fine-tune** and select **Use published checkpoint** to load its task and a held-out scene into Judge. The inference endpoint may still be warming.
5. Once the checkpoint is serving, run `python -m dreamcheck train evaluate --model CHECKPOINT_ID`. This tests both checkpoints on the same three apple holdout layouts, twice per layout by default. The default horizon is 1.2 simulated seconds (12 steps at 10 Hz for the fine-tune and 24 at 20 Hz for its base). Check the report and original runs under `.dreamcheck/`. An unavailable checkpoint stops evaluation and preserves completed results.

The bundled collection recipe is a pilot. Report the number of accepted episodes, the duration and diversity of the demonstrations, and matched held-out outcomes. Keep a fresh evaluation split when repeatedly tuning against earlier holdouts. Compare the physical pass count and failure stage on both policies. Demonstration validation checks dataset structure and collection quality. Measuring improvement requires a completed checkpoint evaluation.

## Two-arm relay

Use `python -m dreamcheck run --reference --task relay` for a local smoke test. In this task, the left arm puts an apple on a transfer plate and the right arm moves it from the plate to the bowl. The judge checks plate contact during the transfer, right-arm pickup, and bowl settlement after release. This is the supported relay from the [bimanual protocol](BIMANUAL_PROTOCOL.md).

Use `python -m dreamcheck train prepare --task relay` to record a separate dataset and `python -m dreamcheck train evaluate --task relay --model CHECKPOINT_ID` to evaluate the resulting checkpoint. The relay varies start, plate, and bowl positions across 49 training and 10 separate holdout layouts. Upload requires 40 accepted training episodes, 8 accepted holdout episodes, and valid splits. Only the training split is uploaded.

While a checkpoint is training, run `python -m dreamcheck train baseline` for 20 base-model trials. The report records the prompt, layout order, budget and per-run evidence. To collect the other half later, run `python -m dreamcheck train evaluate --task relay --model CHECKPOINT_ID --baseline PATH_TO_REPORT`. Both models use the same protocol. The report discloses that the base trials took place earlier.

After a technical interruption, add `--resume-report PARTIAL_REPORT` to the same evaluation command. DreamCheck retains the scored trials and links to the earlier report. Keep the same `--baseline` if the original evaluation used one. For simulator runs DreamCheck clips finite normalized gripper targets to 0–1 when overshoot is at most 0.10 and records the clipping. A larger overshoot or a non-finite target rejects the response before applying any of its steps.

## Probe a checkpoint against its demonstrations

Run the probe when a checkpoint scores below its demonstrations and you need to know where its plans diverge.

```powershell
python -m dreamcheck train probe --task relay --model CHECKPOINT_ID --layout relay-holdout-00
```

DreamCheck replays the scripted demonstration for that layout and asks the checkpoint for a fresh plan every four seconds of the demonstration. Each request starts a new episode on the demonstration's own observation. The report in `.dreamcheck/training/probe-*.json` lists, for every request, how far each arm moves in the demonstration and in the plan, the share of the demonstrated distance the working arm covers, and the mean joint error. **Fine-tune** shows the latest probe for the selected dataset and flags plans that move the resting arm or hold the working one. A probe makes one request per listed moment.

For the relay checkpoint the probe found that the first plan moves the right arm while the demonstration moves the left, and that plans cover a median 37% of the demonstrated distance. The [relay evaluation](../RELAY_EVALUATION.md#demonstration-probe-and-stage-gate) shows how a stage gate and a plan stride of 2 address both findings at inference time.

To evaluate with those aids, add them to the evaluation command. The gate applies to both checkpoints, the stride to the fine-tune, and the limit counts simulated seconds.

```powershell
python -m dreamcheck train evaluate --task relay --model CHECKPOINT_ID --repeats 1 --seconds 150 --stage-gate --stride 2
```

## Three training difficulty levels

| Level | Task | Training recipe | What evaluation measures |
| --- | --- | --- | --- |
| Easy | Apple into bowl with one arm | 500-step pilot | Settled placements on the three held-out layouts |
| Medium | Two-arm apple relay through a transfer plate | 1,000 steps | Plate transfers and settled placements across ten layouts |
| Hard | Sort the requested apple or orange into the named blue or amber bay | 1,000 steps after balanced collection and validation | Full completion, wrong-object movement, and release in the wrong bay across all four prompt conditions |

Use `python -m dreamcheck train prepare --task sorting` to collect the hard split. Upload requires at least ten successful training episodes and two separate holdouts for each object/bay combination. Use `train evaluate --task sorting --model CHECKPOINT_ID` to compare the checkpoint against its base, or select **Two-arm fruit sorting** in Judge. Each layout supplies the object and bay for its prompt. The two objects and two bays are present in every scene.

### Results with the run aids

The first evaluations used the plain policy loop and a wall-clock limit. The follow-up changed how plans are executed and left each checkpoint and prompt as trained. Limits in the follow-up count simulated seconds.

| Task | Plain loop: fine-tune vs Dream-1 Beta | Follow-up: fine-tune vs Dream-1 Beta | Follow-up protocol |
| --- | ---: | ---: | --- |
| Apple placement | 0/6 vs 1/6 | **6/6 vs 0/6** | Plan stride 2 for the fine-tune, no gate, 60 simulated s, 3 layouts × 2 |
| Two-arm relay | 0/20 vs 0/20 | **8/10 vs 0/10** | Stage gate for both, plan stride 2 for the fine-tune, 150 simulated s, 10 layouts × 1 |
| Two-arm sorting | 0/16 vs 4/16 | **8/8 vs 4/8** | Stage gate for both, plan stride 2 for the fine-tune, 150 simulated s, 8 layouts × 1 |

- Each request covered 2.4 simulated seconds: 48 rows at 20 Hz for the base and 24 executed rows at 10 Hz for the fine-tune. Trials ran three at a time with the two checkpoints interleaved. Reports are `evaluation-20261122-113843-793346` (apple), `evaluation-20261122-110257-859250` (relay), and `evaluation-20261122-112552-810179` (sorting) under `.dreamcheck/training/`.
- The apple follow-up uses no simulator signal. The fine-tune's six passes took 27.1–30.4 simulated seconds. The base stopped at pickup four times and transport twice.
- The stage gate reads simulator contacts, so the relay and sorting follow-ups are simulator-assisted. The sorting fine-tune passed all four fruit and bay conditions in 72.6–136.3 simulated seconds. The base moved the unrequested fruit three times and released in the wrong bay once.
- Stride alone was insufficient for sorting. Three fine-tune trials with stride 2 and no gate completed the plate transfer and none completed the right pickup (`1dbaf0d590e3`, `060e57a32398`, `844dc600cf6c`).
- The two columns differ in time basis, action horizon, and trial count, and the follow-up has one or two trials per layout. Treat them as separate experiments. The held-out layouts sit within millimetres of the training layouts.

### Project checkpoints

Dream Labs account access is required to run the published project checkpoints. Each was trained on Dream-1 Beta.

| Task | Checkpoint ID | Held-out plan |
| --- | --- | --- |
| Apple placement | `dream-1-ft:6f167049-dreamcheck-apple-pilot-v1` | 3 layouts × 2 repeats per model, 45-second wall limit |
| Two-arm relay | `dream-1-ft:6f167049-dreamcheck-relay-v1` | 10 layouts × 2 repeats per model, 90-second wall limit |
| Two-arm sorting | `dream-1-ft:6f167049-dreamcheck-sorting-v1` | 8 layouts × 2 repeats per model, 90-second wall limit |
