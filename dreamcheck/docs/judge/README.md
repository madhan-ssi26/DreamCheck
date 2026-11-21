# Judge and Run History

![An offline reference run with a simulator camera, timeline, and placement verdict](../images/judge.webp)

1. In **Judge**, choose **Apple into bowl** or **Orange into bowl**, then select a layout. Stock is the simplest starting point. Workshop adds blocks, a parts tray, and another view.
2. Use **Run scripted reference** for a credential-free smoke test, or enter a prompt and choose **Run Dream-1**. Advanced settings control the checkpoint, executed actions per response, and wall-clock deadline.
3. Watch the overhead, overview, and wrist cameras. Click a response in **Action timeline** to see its evidence. **Show peak lift** opens a saved earlier frame when available.
4. Edit the prompt and retry, or choose the shorter-horizon retry to refresh the model more often. A retry uses the saved run's starting layout, checkpoint, time limit, and goal. The shorter variant halves the prior action horizon.

## How the judge decides

The built-in fruit scorer reads MuJoCo positions, velocity, and contacts after each executed control tick. A pass requires ten consecutive ticks in the bowl region, contact with the bowl, low speed, and no robot contact. Lift and reaching the bowl are progress signals. On a timeout, DreamCheck uses them to identify the last incomplete stage. The scripted reference runs the same physical scorer. The static offline fixture exercises a local scene while holding position.

The timeline stores executed steps, the biggest per-joint correction in a response, the number of clipped steps, request latency, movement, and camera images. A step with several clipped joints counts once. These values describe the simulator run. An imported goal uses the separate [region rule](../scenes/README.md).

## Find or share a result

Open **Run history** and filter by outcome or search by prompt, checkpoint, or scene. Recent saved runs appear after a restart. A run link reopens data stored in that checkout. To send someone a result, share the appropriate JSON and JPEG files from `.dreamcheck/runs/RUN_ID/`. For built-in live timeouts, use **Test a fix** to start a matched [comparison](../compare/README.md).
