# Fine-tune and evaluate

Training is optional. The browser workspace lets you review demonstrations, check dataset or job status, and load a published checkpoint. Use the CLI for dataset preparation, upload, and job submission.

Open **Fine-tune** to see the active bundle's task and validation counts. For a locally recorded job, the job ID is filled in automatically. **Check job status** shows the reported step count when available. **Held-out relay baseline** shows valid trials and the number of timeouts at each first incomplete stage. Select **Inspect example** to open evidence for that stage.

1. Run `python -m dreamcheck train prepare`. The scripted YAM trajectory captures joint state, actions, and three policy cameras at 10 Hz. The physical judge admits successful placements and the SDK validates both LeRobot v2.1 splits. Six layouts are used for training and three for holdout. Read the exported `manifest.json` for actual acceptance and validation results.
2. Run `python -m dreamcheck train upload BUNDLE_PATH --name DATASET_NAME`. This registers or reuses the joint-only DreamCheck YAM embodiment and uploads the training split. Keep the holdout local. Poll with `python -m dreamcheck train status --dataset DATASET_ID` until it reports `processed`.
3. Once you have verified credits, run `python -m dreamcheck train submit --dataset DATASET_ID --suffix dreamcheck-apple-v1 --steps 500`. This schedules a paid job. Poll with `python -m dreamcheck train status --job JOB_ID`. Save the dataset ID, job ID, and eventual published checkpoint ID together.
4. Once the checkpoint is serving, run `python -m dreamcheck train evaluate --model CHECKPOINT_ID`. This checks the embodiment and tests both checkpoints on the same three holdout layouts, twice per layout by default. The default horizon is 1.2 simulated seconds (12 steps at 10 Hz for the fine-tune and 24 at 20 Hz for its base). Check the report and original runs under `.dreamcheck/`. An unavailable checkpoint stops evaluation and preserves completed results.

The bundled collection recipe is a pilot. Report the number of accepted episodes, the duration and diversity of the demonstrations, and matched held-out outcomes. Keep a fresh evaluation split when repeatedly tuning against earlier holdouts. Compare the physical pass count and failure stage on both policies. Demonstration validation checks dataset structure and collection quality. Measuring improvement requires a completed checkpoint evaluation.

## Two-arm relay

Use `python -m dreamcheck run --reference --task relay` for a local smoke test. In this task, the left arm puts an apple on a transfer plate and the right arm moves it from the plate to the bowl. The judge checks plate contact during the transfer, right-arm pickup, and bowl settlement after release. This is the supported relay from the [bimanual protocol](BIMANUAL_PROTOCOL.md).

Use `python -m dreamcheck train prepare --task relay` to record a separate dataset and `python -m dreamcheck train evaluate --task relay --model CHECKPOINT_ID` to evaluate the resulting checkpoint. The relay varies start, plate, and bowl positions across 49 training and 10 separate holdout layouts. Upload requires 40 accepted training episodes, 8 accepted holdout episodes, and valid splits. Only the training split is uploaded.

While a checkpoint is training, run `python -m dreamcheck train baseline` for 20 base-model trials. The report records the prompt, layout order, budget and per-run evidence. To collect the other half later, run `python -m dreamcheck train evaluate --task relay --model CHECKPOINT_ID --baseline PATH_TO_REPORT`. Both models use the same protocol. The report discloses that the base trials took place earlier.

## Sorting reference prototype

Run `python -m dreamcheck.sorting --target orange --bay far_bay` for a contact-only reference. Both fruits and both bays are present. The reference uses the left arm for the pickup, the transfer plate for the handoff, and the right arm for placement. The scorer separately detects displacement of the unrequested fruit and release in the wrong bay. This prototype has no dataset export or live-policy scoring workflow yet.
