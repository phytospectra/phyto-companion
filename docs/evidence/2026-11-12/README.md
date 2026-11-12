# Operational and UI evidence — 2026-11-12 (durability repair, round 2)

Committed here so the evidence survives outside ephemeral `/tmp` storage.
Sanitized before commit: no bearer tokens, HMAC keys, share URLs or other
credentials appear in these files (the exercise runs used explicitly
labelled test-only keys).

| File | Content |
|---|---|
| `ops-exercise-transcript.json` | 18/18 live-process exercise: authenticated admission/replay/conflict, SIGKILL restarts with hydration, approval, durable share revocation across restarts, hot backup → restore into a separate environment with identical replay, restore-audit (replaying keys + would-be-lost runs), bounded load. |
| `soak-summary.json` | 10-minute sustained soak: 7,480 requests, 0 errors, ~12.5 req/s sustained at 8 workers (macOS arm64, single host), latency p50/p90/p95/p99 = 584/1269/1447/1807 ms, store growth 4 KB → 43.7 MB. Throughput degrades as the store grows — the measured single-host ceiling on this hardware/filesystem, not a capacity certification. |
| `ui-upload-mock-desktop.png` / `ui-upload-mock-narrow.png` | Mock-plane browser pass (1280px and 390px, no horizontal overflow). |
| `ui-settings-mock.png`, `ui-login-mock.png` | Mock-plane settings/login surfaces. |
| `ui-integrated-submit.png` | Live research workspace (`/upload`, production AuthProvider path, offline demo identity) against the real authenticated backend: submitted run rendered with `Outcome: awaiting review` and the review checkpoint UI. |
| `ui-integrated-approved.png` | The same workspace after a backend SIGKILL + restart: history refreshed from durable storage, run re-hydrated, review approved → `Outcome: completed`. |
