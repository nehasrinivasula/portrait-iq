# Lab deployment (SCRUM-86)

Runs Portrait IQ as a pod on the lab MiniEdge, so it can reach the Log
Export API (only reachable from inside the cluster). Everything goes through
the `Makefile` at the repo root; `make help` lists the targets.

The lab has no internet: images are built on a laptop, copied over SSH and
imported into the node's container runtime. Commands on the node run as root
through `sudo -i`, as the lab notes require for `kubectl`.

## First time

1. `cp deploy/lab.mk.example deploy/lab.mk` (gitignored).
2. `make api-check`. Read-only, installs nothing. It prints the Log Export
   service's namespace, the node's CPU type and container runtime, and pulls
   one hour of CSV into `dist/api-check/` (real lab data: never commit it).
3. Fill `PLATFORM`, `IMPORT_CMD` and `LOG_EXPORT_NAMESPACE` in `deploy/lab.mk`
   from that output.
4. Give the mentors a heads-up before the first install (GE owns the hardware).

## Install

```
make image-save     # build for the node's CPU type, save to dist/images/
make image-load     # copy to the node, import into the container runtime
make deploy         # namespace (if new), volume, pod, NodePort service
make secrets        # optional: Secret from SECRETS_DIR, outside the repo
make pod-check      # from inside the pod: does the Log Export API answer?
```

Then open `http://192.168.0.3:30080/docs` from a laptop on the lab switch.
`make status` and `make logs` show what the pod is doing.

## Uninstall

`make undeploy` removes the pod, service, Secret and database volume, and
our namespace if `make deploy` created it (a namespace it did not create is
never deleted). Imported images stay in the container runtime; remove them
with the runtime's own command if needed.

## Notes

- **Apple-silicon Macs** build arm64 images by default. `make image` always
  passes `--platform $(PLATFORM)`; building for amd64 on a Mac is slower
  (emulated) but works.
- **If Docker says `docker-credential-desktop` not found**, add Docker
  Desktop's tools to your PATH:
  `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"`.
- **Resource limits** (deployment.yaml): 1 CPU, 1 GiB. The pod shares the
  MiniEdge with the Portrait software; raise only after watching real usage.
- **Database volume** uses the cluster's default storage class. If the pod
  waits on a Pending volume, run `kubectl get storageclass` on the node.
- **Plugin images** (SCRUM-39): save them into `dist/images/` as `.tar.gz`;
  `make image-load` imports every file there.
