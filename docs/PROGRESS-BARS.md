# Where progress indicators belong

An audit of foreground operations that can take long enough for the app to feel
stuck. The GUI already has two useful patterns in `app/static/app.css`:

- `.upbar` for a measured transfer percentage.
- `.workbar` for indeterminate work whose completion percentage is not known.

Use a percentage only when the work reports a real numerator and denominator.
For analysis, builds, network waits, and other work without reliable totals, use
the indeterminate bar and keep the existing job log or current-step message.
Never estimate a percentage from elapsed time or imply that a spinner is exact
progress.

## Highest-priority gaps

| Priority | Operation | Where it starts | Current feedback | Recommendation |
| --- | --- | --- | --- | --- |
| High | Open a saved project | `app/static/js/home.js` (`openRestore`, `/api/restore`) | Dialog says “Opening…” and polls a job log; it can also be cancelled. | Add an indeterminate bar to the restore dialog while the request starts or the restore job is running. Hide it on completion, failure, or cancellation. |
| High | Join a team's room | `app/static/js/project.js` (`openJoin`, `/api/collab/join`) | Dialog log only. Joining may transfer a project, validate the user's game, and build it locally. | Add an indeterminate bar for the join job. Keep it visible through transfer and setup; no percentage unless transfer bytes become observable by the UI. |
| High | Export a rebuildable project | `app/static/js/steering.js` (`exportNow`, `/api/export`) | Export log only. Export can write a source tree and rebuild/compare it. | Add an indeterminate bar beside the export log, hide it in `finally`, and retain the log/result/error behavior. |
| High | Rebuild and compare a game | `app/static/js/steering.js` (`romCheck`, `/api/fork/rom-check`) | Toast and disabled button; no persistent in-place progress. | Add a bar near the button/result, plus a visible job log if the backend emits useful steps. Restore the button and hide the bar on error. |
| High | Measure a project with objdiff | `app/static/js/project.js` and `app/static/js/steering.js` (`/api/fork/check`) | Toast and a disabled button in one path; in another, no persistent feedback. | Show an indeterminate bar at the measurement action, including the “Measure now” first-measure flow. |
| Medium | Start sharing a room | `app/static/js/project.js` (`startShare`, `/api/collab/start`) | Dialog log; button disabled while a local server or tunnel is opened. | Add an indeterminate bar in the share dialog. Tunnel creation has no dependable total, so do not show a percentage. |
| Medium | Reconnect to a room | `app/static/js/project.js` (`openReconnect`, `/api/collab/reconnect`) | Dialog log and disabled button during network communication. | Add an indeterminate bar if reconnection can take noticeable time; keep the error visible in the dialog. |
| Medium | Change a compiler across all source files | `app/static/js/steering.js` (the whole-project compiler job, `/api/compiler`) | A settings log is shown, but no bar. Each file may need to be rebuilt and judged. | Add an indeterminate bar beside the log, visible only for the background whole-project operation. Range-only settings are synchronous and do not need it. |
| Medium | Give uncovered code source files | `app/static/js/project.js` (`startFiles`, `/api/fork/start-files`) | Button changes to “Splitting the game…”; completion/error is only a toast. | Add an indeterminate bar in the notice and show job output if available. This may process many uncovered functions. |
| Medium | Install an agent | `app/static/js/agents.js` and `app/static/js/pages.js` (`/api/agents/install`) | The install log is available, but the install button is disabled with no bar. Installation may download and configure external tools. | Add an indeterminate bar in both installation entry points, next to their logs. |
| Medium | Install a compiler/toolchain | `app/static/js/project.js` (`installToolchain`, `/api/toolchain`) and the import setup in `app/static/js/home.js` | The tools-page flow shows a toast only. During import, the existing import overlay is already busy. | Add a bar to the tools-page install flow. Keep using the import overlay when installation is a prerequisite of importing a game; avoid showing a second competing bar there. |
| Lower | Install an app update | `app/static/js/pages.js` (`showUpdating`, `/api/update`) | Update dialog shows a step and log; it polls while the app installs and restarts. | An indeterminate bar can reassure users before restart. Do not use a percentage unless the updater exposes measured download/install progress. |
| Conditional | Read a community project's policy before forking | `app/static/js/project.js` (`openForkDialog`, `/api/fork/policy`) | The dialog already says it is reading the policy, then displays the result. | The current pending message is sufficient for an ordinary request. If this remote read is measurably slow, use a small indeterminate indicator; it is a preflight request, not the clone/build job. |

## Already covered

| Operation | Existing indicator | Notes |
| --- | --- | --- |
| Open a game file or try the demo | `.upbar` during a measured file upload, then `.workbar` during analysis, in `app/static/js/home.js` | This is the desired two-stage pattern: determinate transfer followed by indeterminate analysis. |
| Create a ROM-mode or scratch project from a game | The same import overlay and `.workbar` as above | Project creation is already covered by the normal game-import path; adding another bar in Settings or the project page would duplicate it. |
| Reimport a game | `.workbar` in the project Settings panel | The bar is shown for the background job and hidden in `finally`. |
| Continue a public decomp | `.workbar` in the fork dialog | Covers the clone/background fork operation. |
| Set up or build a fork after adding its game | `.workbar` in the fork setup panel | A separate `.upbar` is used while uploading a selected game file; the build then gets an indeterminate bar. |

## Operations that generally should not get a bar

- **Continuous agent work, autopilot, polling, notifications, and background
  saves:** these are ongoing or recurring activities rather than a single
  foreground job with a clear start and end. Their current activity, sessions,
  and map state are better feedback than a perpetually moving bar.
- **Short synchronous actions:** saving a note, changing a small setting,
  accepting a room member, or checking a local field should remain immediate.
  If one becomes observably slow, add feedback at that specific action.
- **Passive status refreshes:** project lists, map refreshes, update checks, and
  live polling should not display a blocking bar. They run in the background
  and may overlap normal use.
- **Operations already covered by a parent workflow:** compiler installation
  during game import should use the import overlay instead of adding a second
  progress indicator.

## Implementation guidance

1. Reuse `.workbar` for unknown-duration jobs and `.upbar` only for genuinely
   measured upload/download progress.
2. Put the bar beside the action and its existing job log, so it remains
   visible where the user is waiting.
3. Turn it on before awaiting the job-start request, not only after receiving a
   job ID; otherwise a slow start looks like a dead button.
4. Turn it off for every terminal outcome, including errors and cancellation.
   Restore controls consistently, and keep the failure message/log visible.
5. Label progress bars accessibly. Since the current work bar is indeterminate,
   do not add `aria-valuenow` or a fabricated percentage.
6. Keep messages and logs: a moving bar says work continues, while a current
   step or log tells the user what it is doing.
