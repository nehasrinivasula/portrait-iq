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
- `frontend/` — not yet added to this repo. An interactive HTML prototype
  exists for stakeholder demos; a decision on the real frontend's framework
  (plain JS vs. React) is still open.

## Status

Early skeleton stage. Most of the backend's collectors and integrations
are intentionally unimplemented stubs — see `backend/README.md`'s
"Deliberately not built yet" section for the current list.

## Team

Neha Srinivasula: Product Owner
Ashley Rong: Scrum Master
Hudson Treinen: UX
Abhijay Movva: Demo Coordinator
Chang Zhou: Scribe
Qiancheng Yang: Testing Lead