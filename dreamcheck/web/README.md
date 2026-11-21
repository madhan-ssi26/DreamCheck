# Browser interface

The local server serves a static HTML/CSS/JavaScript app. No Node install, bundler, or CDN is needed. Start it from the [repository root](../../README.md) with `python -m dreamcheck serve`.

| File | Responsibility |
| --- | --- |
| `index.html` and `style.css` | Home, workspace markup, and responsive styling. |
| `workspace.js` | Navigation, deep links, and evidence tabs. |
| `app.js` | Run setup, polling, saved evidence, History, and fine-tuning status. |
| `benchmark.js` | Compare forms, matched results, and exports. |
| `scene-import.js` | ZIP upload, saved scenes, goal preview, and handoff to Judge. |
| `recording.js` | Local video playback, telemetry windows, bookmarks, and evidence export. |
| `replay/` | Standalone offline relay replay from four scripted simulator frames, with clickable stages and arrow-key navigation. |

Check both Home entry points, the bookmark flow, and scene-goal handoff after changing navigation. Keep existing run and comparison links working. Test on Chromium at desktop and narrow viewport sizes. The recording folder picker and bookmark store depend on the browser profile and origin.
