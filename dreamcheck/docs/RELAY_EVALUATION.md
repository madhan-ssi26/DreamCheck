# Two-arm relay evaluation

DreamCheck checked whether fine-tuning improved a scripted YAM relay task. The left arm picks up an apple and places it on a transfer plate. The right arm picks it up from the plate and places it in the bowl.

| Checkpoint | Left pickup | Plate transfer | Full pass |
| --- | ---: | ---: | ---: |
| Dream-1 Beta | 18/20 | 0/20 | 0/20 |
| Relay fine-tune | 9/20 | 0/20 | 0/20 |

The fine-tune produced no gain on the transfer or end-to-end metric in this experiment. The first incomplete stage shifted toward left pickup. This identifies a stage to investigate, but the tally alone cannot determine why it changed. The [demonstration probe and stage gate](#demonstration-probe-and-stage-gate) below explain the stall and take the same checkpoint to 8/10 in a simulator-assisted follow-up.

## Protocol

- The training split had 49 successful scripted demonstrations with joint state, actions, and three synchronized camera views. Ten held-out layouts stayed local.
- Each checkpoint ran twice on every held-out layout, with the same task prompt, 90-second trial cap, and 1.2 simulated seconds per accepted action chunk. The reference policy used stock YAM MuJoCo physics with the adapted relay layout.
- Base-model trials were collected earlier. Gateway warm-up attempts and rejected action responses were excluded from scoring. The evaluation resumed from saved scored trials after interruptions.
- During the resumed simulator trials, DreamCheck clipped small finite gripper overshoots up to 0.10 to the 0–1 range. The earlier scored trials used a 0.05 acceptance tolerance. Both settings apply the same physical bounds to accepted commands. Consider this difference when interpreting the comparison.
- Success requires observable left pickup, plate contact while both arms are clear, right pickup, and settled bowl placement after release.

The [interactive replay](http://127.0.0.1:8765/replay/index.html) shows a scripted completion. The [training guide](training/README.md) covers dataset collection, training, and how to run a new evaluation.

## Demonstration probe and stage gate

The 0/20 result raised a narrower question: had the checkpoint learned the motion, or was it failing in the control loop? DreamCheck answered it with a probe that needs no further training.

`python -m dreamcheck train probe --task relay --model CHECKPOINT_ID --layout relay-holdout-00` replays the scripted demonstration and asks the checkpoint for a fresh plan at 13 moments along it. Each request starts a new episode on the demonstration's own observation, so the reply is the model's plan for that state alone.

| Probe finding on `relay-holdout-00` | Value |
| --- | --- |
| Plans that track the working arm of the demonstration | 8 of 13, mean joint error 0.02–0.07 rad |
| First plan, both arms at home | Left arm moves 0.03 rad where the demonstration moves it 1.55 rad. Right arm moves 0.50 rad where the demonstration keeps it parked. |
| Plans that hold the working arm at a stage boundary | 5 of 13 (start, left release, left park, right grip, right retreat) |
| Distance covered by the working arm in the first half of a plan | Median 37% of the demonstrated distance |

Both relay stages begin with the two arms at the same home pose, and every demonstration keeps the idle arm exactly there. At that pose the checkpoint starts the right-arm stage while the apple is still on the bench. Its plans also cover less distance than the demonstration in the same time. The earlier trials therefore ran out of time with both arms in motion.

Two inference-time changes follow from those findings. Neither changes the checkpoint or the prompt.

- **Stage gate.** The arm with no job in the current stage is held at home, as in every demonstration. The gate reads DreamCheck's plate-transfer check, lets the left arm retreat, parks it, then releases the right arm.
- **Plan stride 2.** The loop executes every second row of each returned plan, so 24 executed rows cover the full 48-row plan.

| Checkpoint, 10 held-out layouts, one trial each | Left pickup | Plate transfer | Right pickup | Full pass |
| --- | ---: | ---: | ---: | ---: |
| Dream-1 Beta with stage gate | 9/10 | 0/10 | 0/10 | **0/10** |
| Relay fine-tune with stage gate and plan stride 2 | 10/10 | 10/10 | 10/10 | **8/10** |

Fine-tuned passes took 73.6–116.4 simulated seconds. Both fine-tuned misses completed the right pickup and did not settle the apple in the bowl before the limit. Nine base trials stopped at plate transfer and one at left pickup. In six base trials the apple reached the bowl without resting on the plate first.

How to read this result:

- Every trial had 150 simulated seconds, the original training prompt, and 2.4 simulated seconds per request (48 rows at 20 Hz for the base, 24 executed rows at 10 Hz for the fine-tune). Trials ran three at a time with the two checkpoints interleaved. The simulated-time limit keeps inference latency and machine load out of the score.
- The gate uses simulator contacts to detect the hand-off. That signal is unavailable on a physical robot without a perception equivalent, so these are simulator-assisted results. They show that the checkpoint holds a usable relay skill and that stage selection at the shared home pose is what the plain loop gets wrong.
- The stride applies to the fine-tune only. The base model ran its native 20 Hz plans under the same gate and the same simulated time.
- This protocol differs from the 90-second wall-clock benchmark above in time basis, action horizon, and trial count. Compare the two tables as separate experiments. The held-out layouts differ from the training layouts by a few millimetres, so the result describes this narrow distribution.
- Two further gated fine-tune trials on `relay-holdout-00` and `relay-holdout-01` passed in 78 simulated seconds before the report started and are excluded from the table. Three earlier probe runs also passed: one with the gate alone in 168.9 simulated seconds on `relay-holdout-00`, and two with the gate and stride 2 in 80.4 and 89.9 simulated seconds on `relay-holdout-03` and `relay-holdout-06`.

The same aids took the sorting fine-tune from 0/16 to 8/8 and a plan stride alone took the apple fine-tune from 0/6 to 6/6. [All three follow-ups](training/README.md#results-with-the-run-aids)

Reproduce with `python -m dreamcheck train evaluate --task relay --model CHECKPOINT_ID --repeats 1 --seconds 150 --stage-gate --stride 2`. In Judge, open a relay timeout and choose **Retry with stage gate**, or choose **Test a fix** to run the plain loop and the gated loop from the same scene. The [27-second comparison clip](demo/relay-stage-gate.mp4) shows the same checkpoint, prompt, and `relay-holdout-03` layout with and without the two changes. It comes from two footage runs outside the report: the plain loop timed out at 150 simulated seconds and the gated run passed at 80.0.

## Longer-budget follow-up

Two additional trials used the trained checkpoint, the original prompt, and the same held-out layout. The wall-clock cap was 120 seconds for both.

| Actions per reply | Simulated seconds executed | First incomplete stage |
| ---: | ---: | --- |
| 12 | 81.4 | Plate transfer |
| 24 | 95.3 | Plate transfer |

The reference finished the same layout in 44.95 simulated seconds. The trained checkpoint lifted the apple in both longer trials and failed to complete transfer. These two diagnostic runs are separate from the 20-trial scores.
