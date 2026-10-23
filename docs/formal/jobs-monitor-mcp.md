# Jobs, monitor, and agent servers

User-visible flows, with what the model and tests found.
Model: `tests/test_model_jobs_monitor.py`. Regression: `tests/test_monitor_fetch_failures.py`.

## Job lifecycle (compat server: crawl, task runs, research)

```mermaid
stateDiagram-v2
    [*] --> Queued: POST create
    Queued --> Running: task starts
    Queued --> Cancelled: DELETE before start
    Running --> Completed: work returns
    Running --> Failed: work raises
    Running --> Cancelled: DELETE while running
    Completed --> Gone: 24h expiry
    Failed --> Gone: 24h expiry
    Cancelled --> Gone: 24h expiry
    Running --> Gone: server restart
    Queued --> Gone: server restart
    Gone --> [*]: poll returns 404

    class Queued,Running,Completed,Failed,Cancelled,Gone works
    classDef works stroke:#393
```

Checked: polls never go backward, terminal states never change, a cancelled job
never reports success or gains pages, only completed jobs return a result.
All hold (376 states; monitor model 996; render gate model 10). Jobs are in memory, so a restart loses them by design.

## Monitor run

```mermaid
stateDiagram-v2
    [*] --> Refetch: monitor run
    Refetch --> Unchanged: page same
    Refetch --> Changed: page differs
    Refetch --> FetchFailed: error or skip
    Refetch --> Removed: HTTP 404 or 410
    FetchFailed --> WronglyRemoved: before fix
    FetchFailed --> NoChange: after fix
    Changed --> Reported: event with stable key
    Reported --> Refetch: next run, same key again

    class WronglyRemoved bug
    class NoChange fixed
    class Unchanged,Changed,Removed,Reported works
    classDef bug stroke:#c33,color:#c33
    classDef fixed stroke:#393,color:#393
    classDef works stroke:#393
```

Bug found and fixed: a URL that failed or was skipped during refresh was missing
from the refreshed pack, so the diff reported it as removed (`removed_count`,
`source_disappeared`, change event). Fix is in `refresh_pack`; only HTTP 404/410
still count as removed. The baseline pack never advances, so a persistent change
is reported on every run with the same idempotency key (consumers dedupe by key).

## Render and paid opt-in

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> LocalBrowser: render_url runtime=local
    Idle --> CloudBlocked: runtime=vercel or e2b, budget 0 or too low
    Idle --> CloudRun: runtime=vercel or e2b, no budget or enough
    Idle --> NoBrowser: website-pack default, trusted env unset
    Idle --> LocalBrowser: website-pack, DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1

    class LocalBrowser,CloudBlocked,NoBrowser,CloudRun works
    classDef works stroke:#393
```

Nothing starts a browser or spends money without an explicit tool argument or
env var. A cloud runtime with no `budget` argument runs uncapped; see the report.
