# Compare

![A saved matched trial report showing pass counts, failure stages, and pair evidence](../images/compare.webp)

Use Compare when you want evidence that a change helps across repeated trials. Choose a baseline and candidate, vary a prompt, checkpoint, or action horizon, and select the layouts and repeats. Confirm the number of live trials before starting. Each trial may make multiple billable Dream-1 calls.

Each pair starts from the same layout. The scheduler alternates which setting goes first and stores completed trials as it runs. For two checkpoints with different policy rates, the scheduler matches their simulated action horizon using the respective number of steps. Stock gives a quick check. Three held-out layouts give broader coverage for the built-in scene tasks.

For a built-in live timeout, **Test a fix** suggests one instruction based on pickup, transport, or release. Review and edit the suggestion, then choose the original scene or the three-scene suite.

## Interpret and export

- Review the matched pass tally and the trial count together. Failed API calls and canceled trials are excluded from the matched pass denominator. A physical timeout counts as a failed attempt.
- **Compare frames** opens paired final or peak-lift frames. Follow either run link to inspect a particular action response and camera.
- Download the CSV for per-trial analysis or get the report JSON at `/api/benchmark?id=COMPARISON_ID`. Reports remain in `.dreamcheck/comparisons/`.
- **Stop comparison** preserves completed trials. Start a new comparison to rerun settings. Imported scenes can be retried in Judge.
