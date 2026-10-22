# Portrait IQ: Project Context for Developers and Agents

Source: "IoMT Analysis Tool Requirements", UW-Madison Capstone Fall 2026, sponsored
by GE HealthCare (5-page scanned PDF, transcribed by hand and OCR on 2026-10-22).
The original PDF is GE-provided material and is intentionally **not** in git
(see `.gitignore`); ask a team lead for a copy. Requirements are subject to
change as the project develops. If this file disagrees with the sponsor's
current document, the sponsor's document wins. Update this file.

Not captured: the PDF we have stops at the last requirement. Pages for
milestones/schedule and the PHI/PII definitions appear to exist in the original
(faint show-through) but are not in our copy.

## 1. What we are building

An intelligent diagnostic platform for GE HealthCare's **Portrait Mobile**
(wearable continuous patient monitoring) ecosystem. It lets field engineers,
support staff and testers collect engineering-grade diagnostic data from a live
system without deep product expertise. It automates:

1. System discovery
2. Kubernetes deployment of analysis tools
3. Network/security configuration
4. Diagnostic data collection
5. Packaging results in a standardized format for engineering review

Why: deployments are global, access-controlled and run 24/7. Diagnosis needs
specialized tools and knowledge, so information gathering is a bottleneck and
onsite engineering visits are expensive.

Users: field engineers, support personnel, testing teams. All requirements are
written as "As a field engineer, I want...".

## 2. Lab environment (Figure 1)

Flat `192.168.0.x` network behind an unmanaged switch. Static IPs are already set
on every device **except the Student Laptop**, which is where the tool runs.

| Device | IP | Notes |
|---|---|---|
| Unmanaged Switch | 192.168.0.1 | hub of a star topology, all links Ethernet |
| MiniEdge P360 | 192.168.0.3 | |
| Virtual Hub Host | 192.168.0.4 | |
| Virtual Hub Self | 192.168.0.100 | second IP on the Virtual Hub, distinct from the host |
| Viewer | 192.168.0.5 | |
| Student Laptop | 192.168.0.6 | runs this tool; address not preset |

## 3. Workflow (Figure 2)

Lanes: Field Engineer, Tool UI, Tool Backend, Live Portrait Mobile System.

1. Engineer launches and configures the tool (UI).
2. UI sends the selected analysis services (e.g. Netshoot, RTI) to the backend.
3. Backend spins up the services on the live system via Kubernetes.
4. Backend collects diagnostic data. Raw network/log/config data returns to the backend.
5. Engineer uses the analysis tools on the live system.
6. Backend **cleans up** the analysis tools on the system.
7. Backend processes and analyzes the data and produces packaged artifacts for engineering review.
8. UI presents a standardized diagnostic package to the engineer.

Implications: cleanup happens before analysis, so raw data must already be held
by the backend. Cleanup must also run after failures and crashes (D-12, D-13).

## 4. Requirements

IDs are ours, added for traceability in tickets and PRs. Wording is condensed
but keeps each requirement's intent.

### Deployment & Configuration
- **D-1** Deploy engineering analysis tools to a live Portrait Mobile environment without manually installing software on each system.
- **D-2** Use the deployed tools on the live system.
- **D-3** Auto-discover Portrait Mobile components (viewer, hub, backend services, Kubernetes nodes) so devices need not be identified by hand.
- **D-4** User supplies connection info (e.g. IP address, credentials); the tool derives network/system settings and configures the analysis tools.
- **D-5** User selects which tools/diagnostics to run, to tailor collection to the issue.
- **D-6** Graphical UI for configuring tools, no command line needed.
- **D-7** Manual override of any auto-generated config value (unique customer environments).
- **D-8** Validate configuration before deployment and before collection begins.
- **D-9** Every open text field validates input and gives clear feedback before configuration or deployment continues.
- **D-10** GUI runs on both **Linux and Windows** workstations.
- **D-11** Tools deploy through Kubernetes using an **Ubuntu 22-based Docker image** where appropriate.
  - **D-11a** Approved exceptions for tools with specialized image dependencies, e.g. **Netshoot**.
- **D-12** Initial release supports the provided tools: **Netshoot, UMF RTI, Wireshark/tshark**.
  - **D-12a** (stakeholder) Team researches and proposes additional diagnostic tools for future prioritization.

### Diagnostic Data Collection
- **C-1** Collect logs, network information, configuration data, Kubernetes diagnostics, Grafana data and system health information.
- **C-2** Real-time progress monitoring of collection.
- **C-3** Notify on completion and on any step failure so the user can act.
- **C-4** Deployed services are removed automatically after collection and use are done; the customer environment returns to its original state.
- **C-5** On session end, prompt the user to choose a cleanup action. After an unexpected crash or interruption, temporary deployments/files/system changes are removed or intentionally preserved, leaving a known clean state.

### Analysis & Reporting
- **A-1** Package collected data into a single transferable file.
- **A-2** Analysis artifacts follow a standardized format, consistent regardless of who collected them.
- **A-3** Packages include metadata about the analyzed environment.
- **A-4** (stretch) High-level summary of detected issues, warnings and system conditions.

### Security
- **S-1** Authentication required before accessing the tool.
- **S-2** UI login session locks/expires after **5 minutes of inactivity**, while backend diagnostic processes **keep running** (no interruption of active collection).
- **S-3** Identify and document where PHI/PII may be collected in the diagnostic workflow.
- **S-4** Log all tool actions and configuration changes for audit.

### Usability
- **U-1** Follow GE HealthCare branding guidelines.
- **U-2** Readable from approximately **1.5 feet** away (field use).
- **U-3** Guided workflows and contextual help; no deep Portrait Mobile expertise required.
- **U-4** Clear error messages with recommended corrective actions.

### Reliability and system impact
- **R-1** Works when **disconnected from the internet** (restricted hospital networks).
- **R-2** Partial results are preserved if a collection process fails.
- **R-3** Minimal impact on the live system; patient monitoring must not be disrupted.
- **R-4** Enforce configurable CPU limits, memory limits, throttling and file-size limits during collection, so the tool does not consume excessive resources or create unmanageable output files in the customer environment.

## 5. Constraints that shape design decisions

- Offline-first (R-1): no runtime dependency on internet services, CDNs or cloud APIs.
- The tool touches live patient-monitoring systems. Default to least impact (R-3, R-4) and make cleanup reliable (C-4, C-5).
- Packaged diagnostics may contain PHI/PII. Never commit them (`.gitignore` blocks `/data/`, `*.zip`, `*.pcap*`), never log secrets, and keep S-3 documentation current.
- GE-provided material (manuals, branding, real log exports, the spec PDF) stays out of git; share via Drive/Slack.
- The idle timeout (S-2) is a UI session concern only. Do not tie job lifetimes to the login session.
- Cross-platform UI (D-10): avoid OS-specific paths and tooling in anything the GUI runs.

## 6. Open items

- Milestone/schedule and PHI/PII definition pages not in our copy of the spec.
- Which additional tools to propose beyond Netshoot, UMF RTI, Wireshark/tshark (D-12a).
- Exact standardized package format (A-2), to be agreed with the sponsor.

## 7. Where things live in this repo

- `README.md` and `backend/README.md`: setup, architecture, implemented vs stubbed.
- `backend/app/`: FastAPI app (`auth.py`, `orchestrator/`, `collectors/`, `ingestion/`, `analysis/`, `audit.py`).
- `frontend/src/`: React UI (`auth/`, `features/`).
- `prototypes/`: stakeholder demo HTML.
- Check `git log origin/main` and the open PRs for current status. This file describes requirements, not progress.
