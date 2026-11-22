# DreamCheck demo

[Download the three-minute demo (1080p MP4)](DreamCheck-submission.mp4)

[![The exact prompt, the checkpoint's plate transfer and bowl placement, and the separate held-out benchmark](walkthrough-preview.png)](DreamCheck-submission.mp4)

The walkthrough shows how to diagnose a failed run, compare prompts, import a scene, and export evidence from a recording. The exact two-arm relay prompt appears at **1:53**. The side-by-side starts at **2:04** and shows the fine-tuned checkpoint completing the left pickup, plate transfer, right pickup, and bowl placement.

- [Side-by-side relay clip (28 seconds)](relay-demo-side-by-side.mp4)
- [Successful checkpoint run (26 seconds)](relay-demo-success.mp4)

The clip shows a selected successful fine-tuned run and a base-model excerpt from a separate run with the same prompt and layout. The earlier time-capped benchmark is documented in the [held-out relay evaluation](../RELAY_EVALUATION.md).

## Exact relay prompt

> Use the left arm to pick up the apple and set it down in the center of the flat transfer plate. Open the left gripper and move the left arm clear of the plate. Once the apple is resting on the plate, use the right arm to pick it up, place it in the bowl, and open the right gripper.
