# Lab setup and deployment (SCRUM-86)

Runs Portrait IQ as a pod on the lab MiniEdge, so it can reach the Log
Export API (only reachable from inside the cluster). Everything goes through
the `Makefile` at the repo root; `make help` lists the targets.

The lab has no internet: images are built on a laptop, copied over SSH and
imported into the node's container runtime. Commands on the node run as root
through `sudo -i`, as the lab notes require for `kubectl`.

**Never put lab passwords, `k3s.yaml`, the etcd database or pulled logs in
this repo, Jira or Slack.** Logins live in the team password manager.

## The bench at a glance

| | |
|---|---|
| MiniEdge address | `192.168.0.3`, SSH as `gehc`, then `sudo su` for `kubectl` |
| OS / Kubernetes | SUSE Linux Enterprise Micro for Rancher, k3s v1.34, single node |
| CPU type | amd64 (`PLATFORM = linux/amd64`) |
| Container runtime | containerd under k3s (`IMPORT_CMD = k3s ctr images import -`) |
| Portrait namespace | `pm` (Log Export service `axone-log-export-tcp`, port 9200) |
| Our namespace | `portrait-iq` (created and removed by `make deploy` / `make undeploy`) |
| Our web page | `http://192.168.0.3:30080/docs` once deployed |
| Diags page (Portrait UI) | `https://192.168.0.3/portrait/diags` (Portrait login required) |

Log Export API facts (tested Oct 2026): no login needed, request times are UTC
on this bench, at most 1 hour per all-devices request (start and end in the
same hour), about 26k rows per hour. Event categories 0–5 are valid; an
invalid one returns HTTP 500.

## Connecting your laptop

The lab switch has no DHCP, so give your ethernet adapter a fixed address:

1. **System Settings → Network → your USB/ethernet adapter → Details →
   TCP/IP.** Configure IPv4: **Manually**. IP `192.168.0.200` (or another
   free `192.168.0.x`), subnet `255.255.255.0`, **Router empty**. Leaving the
   router empty keeps internet on Wi-Fi, which `make image-save` needs.
2. Check: `ping 192.168.0.3`, then `ssh gehc@192.168.0.3`.
3. Run `make` targets in a Terminal **on your laptop**, from the repo folder,
   not inside an SSH session on the MiniEdge. The prompt should be your
   laptop's, not `edge-…`.
4. If a browser says the MiniEdge is unreachable while `curl` works, allow
   the browser in **System Settings → Privacy & Security → Local Network**,
   then quit and reopen it. Use `https://`, since port 80 is closed.

## First time

1. Install Docker Desktop and start it. If Docker says
   `docker-credential-desktop` not found, run
   `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"`.
2. `cp deploy/lab.mk.example deploy/lab.mk` (gitignored). Fill it in from the
   table above, or from `make api-check`.
3. `make api-check`. Read-only, installs nothing. It prints the Log Export
   service's namespace, the node's CPU type and container runtime, and pulls
   one hour of CSV into `dist/api-check/` (real lab data: never commit it).
   `make api-check API_CHECK_HOUR=2026-10-16T20` picks a specific hour.
4. Check the cluster can give our pod storage. On the MiniEdge as root:
   `kubectl get storageclass`. This bench disables k3s's built-in
   `local-storage`, so if nothing is marked `(default)`, the database volume
   will stay Pending and `make deploy` will time out. Tell the team before
   deploying if that's the case.
5. Give the mentors a heads-up before deploying (GE owns the hardware). Check
   memory first (`free -h` on the MiniEdge); the pod can use up to 1 GiB.

## Install

```
make tool-images    # once: Netshoot and tshark tool images for the plugins (needs Wi-Fi)
make ship           # build the backend image, copy every image to the node, import them
make deploy         # namespace (if new), volume, pod, NodePort service, tool-pod RBAC
make secrets        # login files from SECRETS_DIR (outside the repo), restarts the pod
make pod-check      # from inside the pod: does the Log Export API answer?
```

`SECRETS_DIR` (set in `deploy/lab.mk`) is a folder outside the repo with one
`chmod 600` file per value, named exactly `DEMO_AUTH_USERNAME`,
`DEMO_AUTH_PASSWORD` and `AUTH_TOKEN_SECRET`. Make the token secret with
`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Until
`make secrets` has run, the app starts but every route except `/health` and
`/api/plugins` returns 503 (login not configured).

Then open `http://192.168.0.3:30080/docs` from a laptop on the lab switch.
`make status` and `make logs` show what the pod is doing. On an Apple-silicon
Mac, `make image-save` takes several minutes (amd64 is emulated).

## Updating after new code is merged

The image is a snapshot of the code it was built from; the pod keeps running
that version until a new image is loaded. From a checkout of the code you
want:

```
make image-save
make image-load
make deploy
```

Images are tagged with the git commit, so `make status` shows which version
is running. Only rebuild when you need the latest code on the bench (before a
demo, or to test a feature in the cluster), not on every merge.

Old images stay on the MiniEdge (about 470 MB of disk each, no memory).
Clean up now and then, as root on the MiniEdge:
`k3s ctr images ls | grep portrait-iq`, then `k3s ctr images rm <name>`.

## Leaving it running, and stopping it

Once deployed, the pod runs on the MiniEdge, not on your laptop: unplugging
or leaving the lab does not stop it. Kubernetes restarts it if it crashes,
and after a MiniEdge reboot once k3s is back.

To stop it later, any of these:

- **From your laptop**, plugged in: `make undeploy`.
- **From the MiniEdge**, as root, for anyone (teammates, mentors):
  `kubectl delete namespace portrait-iq`. Removes everything of ours in one
  go, and never touches Portrait's own services in `pm`.
- **Pause without removing**, to free memory:
  `kubectl -n portrait-iq scale deployment portrait-iq --replicas=0`
  (`--replicas=1` to start again).

If you leave it running, tell the team and the mentors, and include the
`kubectl delete namespace portrait-iq` command.

## Uninstall

`make undeploy` removes the pod, service, Secret and database volume, and
our namespace if `make deploy` created it (a namespace it did not create is
never deleted). Imported images stay in the container runtime; see "Updating"
for how to remove them.

## Troubleshooting

**After any MiniEdge reboot or k3s restart, check the etcd limit.** The 4 GB
limit is lost when the k3s config is regenerated, and k3s then fails to
start. Step-by-step fix and next steps: [ETCD-QUOTA.md](ETCD-QUOTA.md).

**`kubectl`: connection refused on `127.0.0.1:6443`, or ServiceUnavailable.**
k3s itself is down or still starting. On the MiniEdge as root:

- `journalctl -u k3s --no-pager --since "-3 min" | grep -E 'level=(error|fatal|warn)' | tail -20`
- If it shows `alarm:NOSPACE` / `mvcc: database space exceeded`, etcd (the
  cluster database) is full. This happened in Oct 2026: 2.2 GB of live data
  against the default 2 GB limit, starting around Oct 15 22:00 UTC. The cause
  was not yet found; events are not it. The fix was to raise the limit
  (`etcd-arg: quota-backend-bytes=4294967296`) and then clear the alarm
  (`etcdctl alarm disarm`). Raising the limit alone does not clear the alarm.
- **The k3s config is generated.** `/etc/rancher/k3s/config.yaml` and
  `config.d/` are rebuilt from `/oem/85_cluster_config.yaml` by the appliance,
  so edits made directly in `/etc/rancher/k3s/` are lost. A lasting change
  belongs in the `/oem` file. That's the mentors' call, since it's GE's
  template.
- `etcdctl` needs the k3s certificates. Set them in the same shell:
  `export ETCDCTL_ENDPOINTS=https://127.0.0.1:2379 ETCDCTL_CACERT=/var/lib/rancher/k3s/server/tls/etcd/server-ca.crt ETCDCTL_CERT=/var/lib/rancher/k3s/server/tls/etcd/client.crt ETCDCTL_KEY=/var/lib/rancher/k3s/server/tls/etcd/client.key`,
  then `etcdctl endpoint status -w table` shows size, quota and alarms.
- Do not reboot the MiniEdge while k3s is broken: the Portrait services are
  only still running because they started before it broke.

**`systemctl` says "Failed to get properties: Connection timed out".** The
machine is overloaded. Check `uptime`, `free -h` and `top -b -n 1 | head`,
and wait rather than issuing more restarts.

**Memory is tight** (fans loud, commands slow). The other team's workaround,
on the MiniEdge as root:
`kubectl scale deployment axone-umfcompatibilitycheck --replicas=0 -n pm` and
the same for `axone-registrymanagement`. Use `--replicas=1` to restore them
if someone's testing needs those services.

**`make: command not found`.** You're in an SSH session on the MiniEdge. Run
`make` on your laptop.

## Notes

- **Resource limits** (deployment.yaml): 1 CPU, 1 GiB. The pod shares the
  MiniEdge with the Portrait software; raise only after watching real usage.
- **Database volume** uses the cluster's default storage class (see First
  time, step 4).
- **Plugin images** (SCRUM-39): save them into `dist/images/` as `.tar.gz`;
  `make image-load` imports every file there.
- **Whoever deploys** needs their own `deploy/lab.mk` (copy the example) and
  Docker on their laptop.
