# Portrait IQ

An intelligent diagnostic platform for GE HealthCare's Portrait Mobile
patient-monitoring ecosystem. Portrait IQ automates deployment, configuration,
and execution of engineering analysis tools (Netshoot, UMF RTI, tshark/
Wireshark), collects diagnostic data, and packages it into a standardized
format for engineering review — so field engineers can gather high-quality
diagnostics without deep Portrait Mobile expertise.

UW-Madison Capstone, Fall 2026, sponsored by GE HealthCare.

## Repo layout

- `backend/` — FastAPI backend. See [backend/README.md](backend/README.md)
  for setup instructions, architecture, and what's implemented vs. stubbed.
- `frontend/` — React + TypeScript (Vite). Pages: sign in, environment
  prerequisites, and log import (`/ingest`: where logs can come from, import a
  file or ZIP, what happened). Run the backend, then `cd frontend && npm install
  && npm run dev`; `/api` is proxied to `localhost:8000`. Tests: `npm test`.

## Status

Early skeleton stage. Most of the backend's collectors and integrations
are intentionally unimplemented stubs — see `backend/README.md`'s
"Deliberately not built yet" section for the current list.

## Team

- Neha Srinivasula: Product Owner
- Ashley Rong: Scrum Master
- Hudson Treinen: UX
- Abhijay Movva: Demo Coordinator
- Chang Zhou: Scribe
- Qiancheng Yang: Testing Lead