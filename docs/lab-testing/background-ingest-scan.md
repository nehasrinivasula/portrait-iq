# Lab test: background log pulls and PHI/PII scans

Walkthrough for testing the *background ingest and scan* feature on the bench (branch
`feature/background-ingest-scan`). It proves the acceptance criteria from the story: a large pull and scan run
**without the pod restarting**, `/health` **keeps answering**, the endpoints **return promptly**, progress is
**visible in the UI**, and a failed job is **recorded and shown**.

Read [deploy/README.md](../../deploy/README.md) first for the bench basics (address, SSH, `deploy/lab.mk`,
laptop network setup). Never commit pulled logs, passwords or `k3s.yaml`.

**Time:** about 60 minutes plus the pull. **Tell the mentors first:** this replaces the running backend image on
their bench and the pod restarts once when you deploy.

## 0. Before you go (laptop, no bench needed)

```
git checkout feature/background-ingest-scan && git pull
make test                                   # backend (pytest) and frontend (vitest)
cd frontend && npx tsc --noEmit             # types
```

Everything must pass here first. Run `python -m pytest backend/tests/test_background_tasks.py -q` on its own if you
want just the new tests. If anything fails, fix it before spending bench time.

## 1. Check the bench can take it

On the MiniEdge as root (`ssh gehc@192.168.0.3`, then `sudo su`):

| Check | Command | What you want |
|---|---|---|
| Free memory | `free -h` | At least ~1.5 GiB free. The pod is limited to 1 GiB and the worker counts against it |
| Volume type | `kubectl get storageclass; kubectl -n portrait-iq get pvc` | Local disk. **SQLite WAL is on**; on network storage set `SQLITE_WAL=false` in the deployment before testing |
| Current restarts | `kubectl -n portrait-iq get pod` | Note the `RESTARTS` number now. It must not rise during the test |

## 2. Deploy the branch

On your laptop, from the repo folder, with the branch checked out (the image tag comes from `git rev-parse`):

```
git status                  # must be clean, or the tag ends in -dirty
make ship                   # build, copy and import the image
make deploy                 # applies the manifest, including the new liveness probe
make status                 # pod Running, restarts unchanged
```

Confirm the probe change landed (node, as root):
`kubectl -n portrait-iq get deploy portrait-iq -o jsonpath='{.spec.template.spec.containers[0].livenessProbe}'`
should show `timeoutSeconds: 5` and `failureThreshold: 6`.

## 3. Get ready to watch (three terminals)

```
export BASE=http://192.168.0.3:30080
TOKEN=$(curl -s $BASE/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"<DEMO_AUTH_USERNAME>","password":"<DEMO_AUTH_PASSWORD>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
```

(The login is the one from your `SECRETS_DIR`. Tokens expire; log in again if you get 401.)

- **Terminal A, the heartbeat.** Leave it running for the whole test and watch for slow or failed lines:
  ```
  while true; do curl -s -o /dev/null -w "%{time_total}s HTTP %{http_code}\n" --max-time 5 $BASE/health; sleep 1; done
  ```
  Expect about 0.01–0.05 s. The old behaviour was a burst of `HTTP 000` (connection refused) during a scan.
- **Terminal B, the pod.** `make logs` on the laptop, or on the node `kubectl -n portrait-iq logs -f deploy/portrait-iq`.
- **Terminal C, you.** The commands below, and the UI at `$BASE/` in the browser.

Also on the node, in a fourth window if you can: `watch -n 5 'kubectl -n portrait-iq get pod; kubectl top pod -n portrait-iq'`.

## 4. Test: a scan returns at once and reports progress

First give the scanner something to do. All events are already scanned, so mark them unscanned (node, as root):

```
kubectl -n portrait-iq exec deploy/portrait-iq -- python -c \
 "import sqlite3;c=sqlite3.connect('/data/portrait_iq.db',timeout=30);c.execute('UPDATE normalized_events SET privacy_scan_version=NULL');c.commit();print(c.total_changes,'events reset')"
```

If the database is empty, do section 5 first and come back.

Start the scan and time the request:

```
curl -s -w "\nrequest took %{time_total}s, HTTP %{http_code}\n" -X POST $BASE/api/logs/privacy/scan -H "Authorization: Bearer $TOKEN"
```

| Expect | Why |
|---|---|
| HTTP **202** in well under a second, `"status":"queued"` or `"running"` | The scan is not in the request |
| An `id` in the body | Use it below |

Poll it (every few seconds):

```
TASK=<id from above>
curl -s $BASE/api/tasks/$TASK -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expect `done` and `total` rising, `stage: "Scanning events"`, then `status: "done"` and `result: {"scanned": N}`.

In the UI: open **Logs** and look at the PHI/PII panel. The button should read "Scanning events: x of y". **Reload the
page mid-scan**: the panel should pick the same scan up again, not offer a second one. Click the scan button twice
quickly (or `POST` twice): the second response must carry the **same task id**.

**Heartbeat check:** terminal A must show no `HTTP 000` and no response over about 1 s for the whole scan.

## 5. Test: a large pull

Pick a window that is big enough to hurt. The bench makes about 26,000 rows per hour, so 6 hours is about 150,000
rows (the 2026-11-03 incident was a pull of this kind).

```
curl -s -w "\nrequest took %{time_total}s\n" -X POST $BASE/api/sources/axone/acquire \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"since":"<start UTC>","until":"<end UTC>","ingest":true}'
```

(Or use the Run page with "Collect logs" ticked, and watch the "System logs" panel.) Expect 202 immediately, then
poll `/api/tasks/<id>`: `stage: "Pulling file n of m"`, then `done`, and the `result` has the same file counts the
old response had. Terminal A must stay green. **Also try an upload:** on the Logs page choose *Import log files*
with a CSV from `make api-check`; the button should show progress, then the usual notification.

Pass line for the incident: the same pull that used to cause an `ECONNREFUSED` burst finishes, **`RESTARTS` has not
changed**, and no heartbeat line failed.

## 6. Test: a failure is recorded, not lost

1. **Bad request fails at once, with a reason.** `POST` an acquire for a device id that does not exist, or with
   `until` before `since`. Expect HTTP 4xx, or a 202 whose task becomes `failed` with a readable `error`.
2. **Kill the worker mid-job** (start a big pull, then on the node):
   `kubectl -n portrait-iq exec deploy/portrait-iq -- pkill -f app.tasks.worker`
   Expect the task `failed`, `error: "The worker stopped unexpectedly (exit code …)."`, and the UI showing the
   error. The API must not have restarted.
3. **Restart the backend mid-job:** start a pull, then `kubectl -n portrait-iq rollout restart deploy/portrait-iq`.
   After it comes back the task must read `interrupted` ("The backend restarted before this finished…").
4. **Audit trail:** open the **Audit** page, or `curl -s "$BASE/api/audit?limit=50" -H "Authorization: Bearer $TOKEN"`.
   Expect `background_task_start`, `background_task_done`, `background_task_failed`, `logs_privacy_scan_start`
   and `logs_privacy_scan`, and no log text or file names in any `detail`.

## 7. Record the result

Fill this in for the PR description:

| | Before (main, 2026-11-03) | This branch |
|---|---|---|
| Pull size (hours / rows) | | |
| Request time for acquire / scan | | |
| Slowest `/health` during the job | | |
| `HTTP 000` lines in terminal A | | |
| Pod `RESTARTS` before → after | | |
| Peak pod memory (`kubectl top`) | | |
| Worker kill / backend restart outcomes | | |

If memory gets close to 1 GiB, lower `BACKGROUND_TASK_CONCURRENCY` (already 1) or raise the pod limit, and say so in
the PR.

## 8. Put the bench back

```
git checkout main && git pull && make ship && make deploy     # only if the team wants main back
```

Delete the test pulls if they should not stay: reset or remove collections you created in the UI. Do not copy
anything from `dist/` or the pod into the repo.

## If something goes wrong

| Symptom | Likely cause and first step |
|---|---|
| Task stays `queued` forever | Another task holds the single slot. Check `GET /api/tasks?active=true`; look for the worker in the pod log |
| `database is locked` in the log | WAL or busy timeout not applied (network volume?). Check `SQLITE_WAL` and the storage class |
| Pod `OOMKilled` | The worker pushed the pod over 1 GiB. Raise the limit, then re-test |
| `/health` still slow | Note the time and what the pod was doing. This is the thing the PR is meant to fix, so report it before merging |
| 401 mid-test | Token expired. Log in again |
