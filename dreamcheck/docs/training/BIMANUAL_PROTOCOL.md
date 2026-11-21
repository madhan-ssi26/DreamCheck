# Bimanual training protocol

## Task

**Pick up the transfer block with the left arm, hand it to the right arm in the air, and place it in the marked bay of the divided tray.**

Use a long, lightweight transfer block with two separated grasp zones. Place the pickup on the left, the transfer near the shared workspace, and the tray on the right. The right wrist approaches the exposed end while the left wrist supports the block. Run as a scripted reference first. Use MuJoCo contacts and ordinary joint targets throughout the episode.

The existing processed apple dataset is a single-arm pipeline control. Collect a separate bimanual dataset for this task. Reuse the joint-only YAM contract at 10 Hz with synchronized overhead and wrist cameras. Extend the rollout and evaluator to select the new task explicitly before submitting training.

## Score the sequence

| Stage | Pass condition |
| --- | --- |
| Left pickup | Left gripper contacts and lifts the target clear of the support surface. |
| Transfer | Right gripper contacts the raised target while it is still supported by the left. The left releases and clears. The right retains the object without table contact. |
| Placement | Right releases the target in the requested bay. Its footprint stays inside with tray contact, low speed, and no robot contact for ten consecutive 50 ms ticks. |

Record the latest completed stage and preserve camera frames around the transfer and release. A drop, table-assisted transfer, wrong bay, or placement by the left arm alone fails the task. Tune the grasp and velocity thresholds against synthetic failure cases and a small development pool, then freeze them before evaluation.

## Collection gates

1. Verify both arms can reach their assigned poses and pass the block without wrist or finger collisions. Establish one clean run and capture the handoff from both wrist views.
2. Require at least 8 scripted passes out of 10 varied development resets before collecting at scale. Vary the pickup pose, transfer point and height, target bay, and initial gripper pose within verified workspace ranges.
3. Aim for 40–60 successful demonstrations. Balance the two bay prompts and vary starts. Keep rejected runs and failure reasons out of the training split. Review synchronization, video occlusion during the handoff, and whether both arms produce nontrivial actions.
4. Reserve ten unseen layouts before collecting. Keep their reset seeds, placements, and scene variants out of the training split. Validate the resulting dataset with the Dream Labs SDK and verify the uploaded dataset becomes processed.

If a robust airborne transfer takes too long to establish, use an elevated transfer cradle as a separately named supported relay task. Score left placement in the cradle, right pickup from the cradle, and correct final placement. Keep the two task variants in separate datasets.

## Training and evaluation

- Start with one 1,000-step, joint-only run using cosine decay and `base_share=0.5`. At the event rate of $0.20 per step, this holds $200. Allow roughly nine hours and reserve time for the checkpoint to warm up and for evaluation.
- Compare the checkpoint against its declared base on the ten frozen layouts with two repeats each. Alternate order within pairs and match control horizon in simulated seconds and wall-clock time limits. Report physical passes out of 20 per model alongside pickup, transfer, and placement outcomes. Keep API failures separate from task failures.
- Use one-arm apple or orange placement as a separate retention check. Publish the stage breakdown and representative failure evidence even if overall performance stays flat.
- Consider a second job only once the first evaluation identifies a specific bottleneck and the data or recipe changes to address it. If the reference and data gates miss the event submission window, keep the bimanual reference and scorer as the result.
