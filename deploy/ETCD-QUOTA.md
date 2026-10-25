# Re-applying the 4 GB etcd limit after a k3s restart

**Until the mentors change GE's template, the lab MiniEdge loses the 4 GB
etcd limit whenever its k3s config is regenerated, and k3s then fails to
start.** Check it after every MiniEdge reboot or k3s restart.

## Why this is needed

- etcd (the cluster's database) holds about **2.2 GB** of live data. Its
  default limit is **2 GB**. Over the limit, etcd sets a `NOSPACE` alarm,
  refuses writes, and k3s shuts down on every startup attempt.
- In Oct 2026 the limit was raised to 4 GB in `/etc/rancher/k3s/config.yaml`.
  That file is **generated**: the appliance rebuilds it, and `config.d/`, from
  `/oem/85_cluster_config.yaml`, and the 4 GB setting is not in that template.
  We saw the files rewritten at least once (Oct 20, 19:07 UTC); a reboot
  certainly does it, and a k3s restart may too.
- So after a rebuild, etcd starts with 2 GB again, sees 2.2 GB, and the
  outage comes back.

The permanent fix is adding the setting to `/oem/85_cluster_config.yaml`
(see "Next steps"). Until then, use this runbook.

## 1. Check whether the limit is still in place

On the MiniEdge as root (`ssh gehc@192.168.0.3`, then `sudo su`):

```
grep -c 'quota-backend-bytes' /etc/rancher/k3s/config.yaml
journalctl -u k3s --no-pager -n 2000 | grep -o '"quota-backend-bytes":[0-9]*' | tail -1
```

- `1` and `4294967296`: the limit is in place. Nothing to do.
- `0`, or `2147483648`: the config was regenerated. Continue with step 2.
- Other signs: `kubectl` says `connection refused` or `ServiceUnavailable`,
  and `journalctl -u k3s` shows `alarm:NOSPACE` /
  `mvcc: database space exceeded`.

## 2. Add the setting back

Back up the file, then add the line, only if it's missing (safe to run twice):

```
cp /etc/rancher/k3s/config.yaml /root/config.yaml.bak-$(date +%Y%m%d-%H%M)
grep -q 'quota-backend-bytes' /etc/rancher/k3s/config.yaml || sed -i 's/"disable-network-policy": true,/"disable-network-policy": true,\n  "etcd-arg": ["quota-backend-bytes=4294967296"],/' /etc/rancher/k3s/config.yaml
grep -n -B1 -A1 'etcd-arg' /etc/rancher/k3s/config.yaml
```

The last command must show `"etcd-arg": ["quota-backend-bytes=4294967296"],`
between `disable-network-policy` and `etcd-expose-metrics`. If it shows
nothing, the file's layout changed: stop and add the line by hand, keeping
the JSON style (quotes, brackets, trailing comma).

## 3. Let k3s pick it up

- **If k3s is failing** (restarting over and over), do nothing: it retries
  every ~20 seconds and reads the file on the next attempt.
- **If k3s is running normally,** it only reads the file at startup:
  `systemctl restart k3s`. Portrait's pods keep running during the restart.

After about 30 seconds, confirm:

```
journalctl -u k3s --no-pager -n 500 | grep -o '"quota-backend-bytes":[0-9]*' | tail -1
```

It must say `4294967296` before going on.

## 4. Clear the alarm, if it's set

Raising the limit does **not** clear an existing `NOSPACE` alarm. Set up
`etcdctl` in the same shell:

```
export ETCDCTL_ENDPOINTS=https://127.0.0.1:2379 ETCDCTL_CACERT=/var/lib/rancher/k3s/server/tls/etcd/server-ca.crt ETCDCTL_CERT=/var/lib/rancher/k3s/server/tls/etcd/client.crt ETCDCTL_KEY=/var/lib/rancher/k3s/server/tls/etcd/client.key
```

Look first. This waits until etcd answers, then shows size, quota and alarms:

```
until etcdctl endpoint status -w table 2>/dev/null; do sleep 1; done; etcdctl alarm list
```

- The QUOTA column should show about 4.3 GB. If it shows `0 B` (meaning the
  2 GB default), go back to step 3.
- If `alarm list` shows `alarm:NOSPACE` (and **not** `CORRUPT`), clear it:

```
until etcdctl alarm disarm 2>/dev/null; do sleep 1; done; etcdctl alarm list
```

An empty `alarm list` means it's cleared. If you ever see `alarm:CORRUPT`,
stop and tell the mentors: that's data damage, not a full database.

## 5. Confirm the cluster is back

```
kubectl get nodes
```

The node should be `Ready`. Then:

- If memory is tight, re-apply the scale-down from `deploy/README.md`
  (Troubleshooting), since a restart may bring those services back.
- If our pod was deployed, `kubectl -n portrait-iq get pods` should show it
  running again.
- Post in the team Slack that the limit was re-applied, and when.

## Next steps (for the mentors and the team)

1. **Make it permanent.** Add `etcd-arg: quota-backend-bytes=4294967296` to
   `/oem/85_cluster_config.yaml`, in that file's own format, so regenerated
   configs keep it. That's GE's template, so it's the mentors' change to make
   or approve. Once done, this runbook is no longer needed.
2. **Find what filled etcd.** It's live data (only ~1% reclaimable), growing
   from about Oct 15 22:00 UTC, and not events (~2.5k). Count keys by type
   with `etcdctl` set up as in step 4:
   `etcdctl get /registry --prefix --keys-only | awk -F/ 'NF{print $3}' | sort | uniq -c | sort -rn | head -15`.
   A type with a huge count is the likely cause; if counts look normal, look
   for a few very large objects.
3. **Watch the size.** `etcdctl endpoint status -w table` shows DB SIZE.
   If it keeps growing toward 4 GB, the alarm will trip again at the new
   limit. Check it weekly, or whenever the bench is in use.
4. **After cleanup, reclaim space** with `etcdctl defrag` (etcd pauses
   briefly while it runs).
