# Portrait IQ

An intelligent diagnostic platform for GE HealthCare's Portrait Mobile
patient-monitoring ecosystem. Portrait IQ automates deployment, configuration,
and execution of engineering analysis tools (Netshoot, UMF RTI, tshark/
Wireshark), collects diagnostic data and logs, and packages it into a
standardized format for engineering review, so field engineers can gather
high-quality diagnostics without deep Portrait Mobile expertise.

UW-Madison Capstone, Fall 2026, sponsored by GE HealthCare.

## Status

Working end to end for logs, in progress for the rest. What exists today:

| Area | State |
|---|---|
| Login and session handling | Working. Local demo login (not GE SSO), signed tokens, 5-minute idle lock, audit log |
| Connectivity preflight | Working. Bounded TCP checks against a target before diagnostics start |
| Log acquisition | Working. Local files/folders/ZIPs and uploads; Box (tested against a mock); the Axone log export API (tested on the lab system). Exact time frames, device selection, only-new-files, size and time limits |
| Common log format | Working. Portrait CSV and Kubernetes pod logs convert to one event shape; every row is either stored or counted as rejected |
| Central log storage and search | Working. One query across every source and device (time, device, source, category, severity, text), cursor paging, facet counts |
| Unified Log View | Working. All entries of a collection in one time-ordered stream, with a completeness report that names anything missing |
| Tool plugins | Netshoot and tshark run as Kubernetes pods. UMF RTI and hub import are stubs |
| Analysis | Five findings from the sample logs. Manual matching always returns "unconfirmed" until real manuals are ingested |
| Deployment | Image build and lab deploy to the MiniEdge via `make` (see `deploy/README.md`) |
| Frontend | React app with login, prerequisites check, and a system info panel. Collection and log screens are not built yet |

What is deliberately not built yet is listed in
[backend/README.md](backend/README.md#deliberately-not-built-yet).

## Repo layout

- `backend/`: FastAPI backend. [backend/README.md](backend/README.md) has the
  setup steps, every endpoint, and how each part works.
- `frontend/`: React + TypeScript + Vite app. `/api` is proxied to the backend
  in development.
- `deploy/`: Kubernetes manifests, tool image build, and lab scripts.
  [deploy/README.md](deploy/README.md) walks through the lab setup.
- `docs/`: [PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) (requirements,
  lab diagram, workflow) and [plugins-api.md](docs/plugins-api.md) (plugin
  endpoints for the frontend).
- `Makefile`: one-command targets for setup, tests, local run and lab deploy.
  `make help` lists them.

## Quick start

You need Python 3.12 and Node.js.

```bash
make setup      # creates backend/.venv, installs dependencies and the secret-scanning hook
make test       # backend and frontend tests
make dev        # backend at http://localhost:8000/docs
```

The backend needs a local login before you can use any endpoint. Copy
`backend/.env.example` to `backend/.env` and set `DEMO_AUTH_USERNAME`,
`DEMO_AUTH_PASSWORD` and `AUTH_TOKEN_SECRET` to values you make up for your own
demo (never real or GE credentials). `backend/.env` is gitignored. Then open
`/docs`, run `POST /api/auth/login`, and click Authorize with the token.

Frontend, in a second terminal:

```bash
cd frontend
npm run dev     # http://localhost:5173
```

To run on the lab MiniEdge, follow [deploy/README.md](deploy/README.md).

## How the pieces fit

```
Frontend  ->  Orchestrator API  ->  Collectors (tool plugins, run as pods)
                   |                        |
                   |                  Log sources (local, Box, Axone export)
                   v                        v
            Event store  <-  Common log format (parsers)
                   |
        Analysis rules -> Knowledge lookup -> Findings
```

Every log, whichever source or format it came from, is converted to the same
event shape (UTC time, device, source, category, severity, process, message,
and the file and line it came from). Search, the unified view and the analysis
rules all read only that shape. Adding a log source or a plugin is one new
file and one registry line.

## Data and privacy

Raw logs can contain patient or staff identifiers. Treat any log text as
sensitive.

- Never commit real log exports, packet captures, GE service manuals, the
  sponsor's requirements document, branding assets, or anything pulled from a
  real hospital deployment. Treat GitHub as public.
- Never commit credentials, `k3s.yaml`, or the etcd database. Secrets live
  outside the repo (see [backend/SECRETS.md](backend/SECRETS.md)); a
  pre-commit hook (`gitleaks`) blocks commits that contain them.
- Local data (`portrait_iq.db`, `data/`, `dist/`) is gitignored. Delete it when
  you finish with real lab data.
- The audit log records who did what, never file contents, search text or
  secrets.

## Contributing

1. Branch from the latest `main` (`git pull` first).
2. Keep changes small and add tests next to what you change.
3. Run `make test` before you push.
4. Open a pull request; a teammate reviews it before it is merged.

If a change adds a column to a table, say so in the PR: the local database
only holds development data, so teammates should delete `portrait_iq.db` and
restart.

## Team

- Neha Srinivasula: Product Owner
- Ashley Rong: Scrum Master
- Hudson Treinen: UX
- Abhijay Movva: Demo Coordinator
- Chang Zhou: Scribe
- Qiancheng Yang: Testing Lead
