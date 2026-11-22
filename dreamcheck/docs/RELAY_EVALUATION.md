# Two-arm relay evaluation

DreamCheck checked whether fine-tuning improved a scripted YAM relay task. The left arm picks up an apple and places it on a transfer plate. The right arm picks it up from the plate and places it in the bowl.

| Checkpoint | Left pickup | Plate transfer | Full pass |
| --- | ---: | ---: | ---: |
| Dream-1 Beta | 18/20 | 0/20 | 0/20 |
| Relay fine-tune | 9/20 | 0/20 | 0/20 |

The fine-tune produced no gain on the transfer or end-to-end metric in this experiment. The first incomplete stage shifted toward left pickup. This identifies a stage to investigate, but the tally alone cannot determine why it changed.

## Protocol

- The training split had 49 successful scripted demonstrations with joint state, actions, and three synchronized camera views. Ten held-out layouts stayed local.
- Each checkpoint ran twice on every held-out layout, with the same task prompt, 90-second trial cap, and 1.2 simulated seconds per accepted action chunk. The reference policy used stock YAM MuJoCo physics with the adapted relay layout.
- Base-model trials were collected earlier. Gateway warm-up attempts and rejected action responses were excluded from scoring. The evaluation resumed from saved scored trials after interruptions.
- During the resumed simulator trials, DreamCheck clipped small finite gripper overshoots up to 0.10 to the 0–1 range. The earlier scored trials used a 0.05 acceptance tolerance. Both settings apply the same physical bounds to accepted commands. Consider this difference when interpreting the comparison.
- Success requires observable left pickup, plate contact while both arms are clear, right pickup, and settled bowl placement after release.

The [interactive replay](http://127.0.0.1:8765/replay/index.html) shows a scripted completion. The [training guide](training/README.md) covers dataset collection, training, and how to run a new evaluation.
