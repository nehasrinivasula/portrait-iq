# Lab test: tool output scanned in the export (S-3)

Walkthrough for testing, on the bench, that **Netshoot and tshark output in the export zip is scanned like logs**
and reported in `privacy/report.json` (branch `feature/phi-collection-points`). It also covers the review of the
collection-point document.

Read [deploy/README.md](../../deploy/README.md) for bench basics. **Use made-up patient data only.** Real PHI must
never go into a test, a screenshot, Slack or this repo; the planted values below are fake on purpose.

**Time:** about 40 minutes. Only the backend image changes; no new tool images are needed.

## 0. Before you go (laptop)

```
git checkout feature/phi-collection-points && git pull
cd backend && .venv/bin/python -m pytest tests/test_packaging.py tests/test_phi_collection_points.py -q
```

Both files must pass. `test_phi_collection_points.py` also fails if a collector or log source is missing from the
document, which is the check you want.

## 1. Deploy the branch

```
git status           # clean, or the image tag ends in -dirty
make ship && make deploy && make status
```

```
export BASE=http://192.168.0.3:30080
TOKEN=$(curl -s $BASE/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"<DEMO_AUTH_USERNAME>","password":"<DEMO_AUTH_PASSWORD>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
```

## 2. Make a run that has tool output

1. Get a capture that is **not from a hospital**: on your laptop `sudo tcpdump -c 50 -w /tmp/sample.pcap` (or a public
   Wireshark sample capture).
2. In the UI **Run** page choose **Netshoot** and **tshark**. For tshark pick *import* mode and upload `sample.pcap`.
   Target the bench (`192.168.0.3`).
3. Wait until the run is **Done**. Note its run id (shown on the run page, also the folder name below).

## 3. Plant fake patient data in the tool output

Real tool output on the bench has none, so add canary lines to prove the scanner sees them. On the node as root:

```
JOB=<run id>
kubectl -n portrait-iq exec deploy/portrait-iq -- sh -c \
 "printf 'note: patientName=Jane Testcase mrn=TEST-000123\nssn 123-45-6789\nreach 10.1.2.3 and aa:bb:cc:dd:ee:ff\n' >> /data/jobs/$JOB/netshoot/netshoot.txt; ls /data/jobs/$JOB/*"
```

(If the file name differs, the `ls` shows it. Use whichever text file Netshoot or tshark wrote.)
Expected planted findings: one line with a **name and MRN** (two flags), one **SSN**, one line with an **IP and a MAC**
(network identifiers, not "flagged").

## 4. Masked export (the default)

In the UI: **Export** page, masking on, **Build the package**. Or by API:

```
curl -s -X POST $BASE/api/jobs/$JOB/package -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"mask":true}'
curl -s $BASE/api/jobs/$JOB/package -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -E '"state"|detail|warnings' 
curl -s $BASE/api/jobs/$JOB/package/download -H "Authorization: Bearer $TOKEN" -o /tmp/masked.zip
```

Check the zip (needs `unzip` and `jq`):

```
unzip -p /tmp/masked.zip privacy/report.json | jq .tools
unzip -p /tmp/masked.zip tools/netshoot/netshoot.txt | tail -5
unzip -p /tmp/masked.zip tools/results.json | head -30
```

| Check | Expect |
|---|---|
| `tools.scanned` | `true` |
| `tools.flagged_lines` | at least 2 (the name/MRN line and the SSN line) |
| `tools.by_category` | includes `ssn` and the name and MRN kinds; **no values anywhere in the report** |
| `tools.network_identifier_lines` | at least 1; `network_by_category` has `ip_address` and `mac_address` |
| the netshoot file in the zip | the SSN and the name/MRN replaced by `[REDACTED:…]`; the IP and MAC still there |
| the tshark capture (`capture.pcapng`) | **not in the zip**; `tools.files` lists it with `included: false` and a "binary" reason |
| `tools.complete` | `true` (the one file that could not be scanned is not in the zip) |
| the Export page | the Diagnostic results step says how many text files were scanned and how many had PHI / PII |

Search the whole zip for the canaries: `for f in $(unzip -Z1 /tmp/masked.zip); do unzip -p /tmp/masked.zip "$f" | grep -l -E '123-45-6789|Jane Testcase' >/dev/null && echo "LEAK in $f"; done`.
Nothing should print.

## 5. Unmasked export

Export again with masking **off** (UI toggle, or `{"mask":false}`) and download to `/tmp/unmasked.zip`.

| Check | Expect |
|---|---|
| warnings on the Export page | "Masking was turned off…" for the logs **and** a line saying diagnostic output with PHI / PII is in the zip as is |
| warning about the capture | `capture.pcapng is in the zip but could not be scanned for PHI / PII (binary file…)` |
| `unzip -p /tmp/unmasked.zip privacy/report.json | jq .tools.complete` | `false` |
| the capture | **is** in the zip, and listed with `scanned: false` |
| the canary values | present in the netshoot file (unmasked means as is) but still **counted** in the report |

## 6. Failure behaviour

- Delete the netshoot file from the pod, export again: the file is reported as left out ("no longer on the server"),
  and the export still builds.
- Optional: add a non-UTF-8 file to the run folder and list it in `results.json`; it should appear with
  `scanned: false`, and be left out of a masked export.

## 7. Review the collection-point list with the mentors

Open [PHI-PII-COLLECTION-POINTS.md](../PHI-PII-COLLECTION-POINTS.md) and walk through it with Andrew or Francisco.
Questions that only they can answer, to write into the "what remains" cells:

1. **RTI:** can UMF samples or topic names carry patient identifiers on a real bench? Is the `rtiddsspy` output as the
   docs say (row 6 is not yet checked against the bench)?
2. **Kubernetes events and Grafana (rows 8 and 9):** what will the collectors actually read? Do Grafana panels or
   annotations ever carry patient, room or device-owner names?
3. **Logs from a real hospital:** any fields besides `description` that carry names or ids?
4. **Hub logs pulled by hand:** is the format the same as the Axone export, or does it need its own scan rules?

Record the answers in the document and open a follow-up for anything that is "remains".

## 8. Clean up

Delete `/tmp/masked.zip` and `/tmp/unmasked.zip` (the unmasked one holds the canary lines), delete the test run, and
put the bench back to `main` if the team wants it (`git checkout main && make ship && make deploy`).

## Record the result

| | Result |
|---|---|
| Masked: canaries absent from every file | |
| Masked: counts in report match what was planted | |
| Capture left out and listed | |
| Unmasked: warnings shown, `tools.complete` false | |
| Mentors' answers recorded in the document | |
