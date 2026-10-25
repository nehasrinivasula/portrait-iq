# Secrets handling (SCRUM-72)

How the IoMT Analysis Tool stores, loads and protects credentials.

**Three rules**
1. Secrets come from the environment or a file outside the repo. Never from code.
2. A secret value never appears in a log, an error message, or an exported zip.
3. Error messages name the secret (`BOX_CLIENT_SECRET`), never its value.

Code: `backend/app/secrets_loader.py`. Tests: `backend/tests/test_secrets_loader.py` (run `pytest` from `backend/`).
The module is deliberately not called `secrets.py`, which would shadow Python's built-in `secrets`.

---

## 1. Inventory

| Name | Used by | Notes |
|---|---|---|
| `BOX_CLIENT_ID` | Box connection (SCRUM-43) | **Box auth type still to be confirmed** with the Box admin. Assumes a client ID + secret for now. |
| `BOX_CLIENT_SECRET` | Box connection (SCRUM-43) | Same. |
| `PLATFORM_DIAGS_USERNAME` / `PLATFORM_DIAGS_PASSWORD` | Log download from the Portrait Mobile platform (`/portrait/diags`) | **TBD. Only needed if that page asks for a login.** Open it in a browser to find out. |
| *(none: cluster access)* | Deploying plugins (SCRUM-39) | **The tool runs inside the cluster**, so it uses its own ServiceAccount and no kubeconfig is stored. It needs a least-privilege Role instead (section 4). The SSH login to the MiniEdge is for people, not the tool. |
| `VIEWER_LOGIN_USERNAME` / `VIEWER_LOGIN_PASSWORD`, `HUB_LOGIN_USERNAME` / `HUB_LOGIN_PASSWORD` | Viewer / Hub sign-in | **Only if a plugin signs in programmatically.** TBD. |
| `DEMO_AUTH_USERNAME` / `DEMO_AUTH_PASSWORD` | Demo login (SCRUM-32) | Team test login for the lab, not a real person's password. In the pod: files from `make secrets` (deploy/README.md). |
| `AUTH_TOKEN_SECRET` | Signs login tokens (SCRUM-32) | Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SESSION_SIGNING_KEY` | Session lock (SCRUM-34) | Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |

Not in this list: **user passwords.** The Login story (SCRUM-32) should store a salted hash, never the password.

Add a row whenever a new credential is introduced.

### How the prototype runs

The tool runs **inside the Kubernetes cluster on the MiniEdge**, next to the hub host and the viewer. Team members reach it from their **laptops**, which connect through an unmanaged switch over ethernet: a browser for the tool's UI, and SSH to the MiniEdge by hand when they need `kubectl`. So the laptop is only the client, and the tool's own secrets (Box, signing key) live in the cluster, not on laptops.

Two things follow:
- The tool reaches the cluster with its own ServiceAccount (section 4), not with anyone's SSH login or kubeconfig.
- Credentials typed into the app (for example the IP address and login on the first screen) travel from the laptop to the tool. Keep them **in memory only**, call `register_for_redaction` on them, never write them to disk, logs or exports.

### Where logs come from: three modes

Per the team, logs are normally saved on the Portrait Mobile platform itself and downloaded by service from its diagnostics page (`http://<PLATFORM_IP>/portrait/diags`, then Asset Management, then the log download page). Box is not part of the standard process: some teams put files there, others do not. So the tool supports:

- **Platform mode** (primary): pull logs from the platform diagnostics page over the local network. No internet is needed. It needs a login only if that page requires one (see the inventory row above).
- **Box mode** (optional): pull logs by time range from Box. Needs outbound internet access and Box credentials.
- **Import mode**: load log files someone downloaded elsewhere. Needs no secret.

The tool must start and work with none of these credentials set, so load each one with `load_secret(NAME, required=False)` and treat `None` as "this mode is off". Everything pulled or imported goes through the same export check.

### Lab logins: values are never stored here

The lab logins (Viewer, Hub, MiniEdge, VM console) are shared test-bench logins that people use by hand. Their **values** never go in the repo, in this file or in Jira. Keep them in a team password manager. The tool does not need them unless a plugin signs in to the Viewer or Hub programmatically; if one does, add a row to the inventory and load it with `load_secret`. The lab logins are for the test bench only and should not be reused for any real system.

## 2. Naming and loading rule

For a secret called `NAME`, the loader checks:

1. environment variable `NAME` (the value itself), else
2. environment variable `NAME_FILE` (the path to a file containing the value).

Setting both is an error. A trailing newline in the file is ignored. Secret files must be owner-only (`chmod 600` or `0400`) and owned by you (or root). Group read (`0440`/`0640`) is accepted only when the file's group is the numeric ID in `SECRETS_FILE_GID`, which is meant for a Kubernetes Secret volume with `fsGroup` (see below).

## 3. Using it in code

```python
from app.secrets_loader import (
    load_secret, install_log_redaction, register_for_redaction, redaction_scope,
    assert_no_secrets,
)

install_log_redaction()                      # once, at startup, before any logging

box_id = load_secret("BOX_CLIENT_ID")
box_secret = load_secret("BOX_CLIENT_SECRET")

# only at the point where the value is actually sent to Box:
payload = {"client_id": box_id.reveal(), "client_secret": box_secret.reveal()}

# values you only learn at runtime (e.g. the access token Box returns) are NOT loaded
# by load_secret, so register them yourself:
register_for_redaction(access_token, "BOX_ACCESS_TOKEN")

# a credential that only matters during one request (e.g. the password typed on
# the Connect screen): masked while the block runs, then forgotten, so the
# process does not keep every password ever entered.
with redaction_scope(req.password.get_secret_value(), "CONNECT_PASSWORD"):
    ...

# in the export code (SCRUM-61), before and after building the zip:
assert_no_secrets([staging_dir])
assert_no_secrets([zip_path])
```

`print(box_secret)` shows `Secret(BOX_CLIENT_SECRET=***)`, not the value.

## 4. Setup

### Local / dev only: one file per secret, outside the repo

(Use this when you run the tool on your own laptop while developing. When it runs in the cluster, use the Kubernetes path below.)

```bash
mkdir -p ~/iomt-secrets && chmod 700 ~/iomt-secrets

# read -s keeps the value out of your terminal and shell history
read -rs -p "Box client secret: " V && printf '%s' "$V" > ~/iomt-secrets/BOX_CLIENT_SECRET; unset V; echo
chmod 600 ~/iomt-secrets/BOX_CLIENT_SECRET

export BOX_CLIENT_SECRET_FILE=~/iomt-secrets/BOX_CLIENT_SECRET
```

Repeat for each secret. `secrets.example.env` lists the names with fake values.

### Kubernetes (how the tool gets its secrets in the cluster)

Create the Secret from files, so real values never go into a manifest in git:

```bash
kubectl create secret generic iomt-secrets \
  --from-file=BOX_CLIENT_ID=$HOME/iomt-secrets/BOX_CLIENT_ID \
  --from-file=BOX_CLIENT_SECRET=$HOME/iomt-secrets/BOX_CLIENT_SECRET \
  --namespace <namespace>
```

Mount it as files and point the `_FILE` variables at them (add this to the pod spec):

```yaml
spec:
  containers:
    - name: iomt-tool
      env:
        - name: BOX_CLIENT_ID_FILE
          value: /var/run/secrets/iomt/BOX_CLIENT_ID
        - name: BOX_CLIENT_SECRET_FILE
          value: /var/run/secrets/iomt/BOX_CLIENT_SECRET
      volumeMounts:
        - name: iomt-secrets
          mountPath: /var/run/secrets/iomt
          readOnly: true
  volumes:
    - name: iomt-secrets
      secret:
        secretName: iomt-secrets
        defaultMode: 0400
```

Things to know:
- If the container runs as a **non-root user**, `0400` makes the files unreadable to it. Set `securityContext.fsGroup`, use `defaultMode: 0440`, and set the env var `SECRETS_FILE_GID` to the same number as `fsGroup`. The loader then accepts group read for files in that group only.
- Kubernetes Secrets are **base64-encoded, not encrypted**, unless encryption at rest is enabled. Limit who can read them with RBAC, and ask the cluster admin whether encryption at rest is on.

### ServiceAccount and RBAC (cluster access)

Because the tool runs inside the cluster, it does not need a kubeconfig. Kubernetes gives the pod a token for its **ServiceAccount**, and the Kubernetes client library can use it directly (for the Python client, `config.load_incluster_config()`). That removes one secret, but the token is still a credential: whatever the Role allows, anyone who compromises the tool can do. So keep the Role small.

The real one is `deploy/k8s/tool-rbac.yaml` (SCRUM-39): ServiceAccount `portrait-iq`, and a Role in our own namespace with only what the Netshoot and tshark plugins use: pods create/get/list/delete and pods/exec. The tool pods it starts get no token at all (`automountServiceAccountToken: false`). Exec is the broadest of these rights: it runs commands in any pod of our namespace, which is why tool pods never share that namespace with anything else. **Ask the cluster admin to review it.**

Keep it tight:
- Use a **Role** (one namespace), not a ClusterRole, unless the plugins truly need more.
- Add only the verbs the plugins use. Removing "delete" or "create" later is easy; finding out the Role was too broad is not.
- For local development against a cluster, use your own `~/.kube/config`. Never copy it into the repo.

## 5. Keeping secrets out of git

```bash
# already applied to the repo .gitignore on this branch
pip install pre-commit && pre-commit install  # uses .pre-commit-config.yaml (gitleaks)

# one-time check of the whole existing history (install gitleaks first):
gitleaks detect --source . --log-opts="--all"
```

If a real credential was ever committed, **rotate it** (section 6). Removing it from history does not make it safe again.

## 6. Rotation (no code change needed)

1. Create the new credential (in Box, or generate a new key).
2. Replace the secret: overwrite the local file, or for Kubernetes re-run `kubectl create secret generic iomt-secrets ... --dry-run=client -o yaml | kubectl apply -f -`.
3. Restart the tool so it reloads: for Kubernetes, `kubectl rollout restart deployment/<name>`. (Secrets are read at startup.)
4. Confirm it works, **then** revoke the old credential.

If a secret leaks: rotate first, investigate second.

## 7. Rules for contributors

- Get secrets only through `load_secret`. Call `.reveal()` only where the value is used.
- Call `register_for_redaction` for any token you obtain at runtime.
- Never pass a secret as a command-line argument to a tool or plugin (it shows in `ps`). Use an environment variable or a mounted file.
- Never put a secret value in an exception message. Use the secret's name.
- Never write secrets, tokens, or kubeconfigs into the logs or output the zip export collects.
- Never pass a password on a command line (it shows in `ps`). Use an environment variable or a mounted file.
- Credentials typed into the UI stay in memory only (see "How the prototype runs").
- Do not pass the tool's own secrets (Box credentials, the signing key) into the diagnostic pods it launches. Set `automountServiceAccountToken: false` on those pods unless a plugin needs cluster API access.

## 8. Limits to be aware of

- Redaction and the export scan match **exact text**. They will not catch a secret that has been transformed (URL-encoded, base64, split across log lines).
- Values shorter than 6 characters are not registered, because masking them would mangle ordinary text.
- The export scan checks registered values plus a few high-confidence patterns (private keys, bearer tokens). It reduces the risk of a leak; it does not prove there is none.
- The scan reads every file, so very large logs add time to an export.

## 9. Open items

- [ ] Confirm the Box auth type and update the inventory (see the questions list for the Box admin).
- [ ] Confirm with compliance whether Kubernetes Secrets / a secrets file are acceptable storage.
- [ ] Wire `assert_no_secrets` into the zip export when SCRUM-61 is built.
- [x] Where the tool runs: **inside the cluster on the MiniEdge**. Team members connect from their laptops over ethernet through an unmanaged switch. No kubeconfig is stored; the tool uses a ServiceAccount.
- [ ] Have the cluster admin review the Role in `deploy/k8s/tool-rbac.yaml` (namespace `portrait-iq`, pods and pods/exec).
- [ ] Only if Box mode is wanted: confirm pods in the cluster can reach Box. The lab switch may have no internet connection, in which case Box mode will not work there.
- [ ] Open `http://<PLATFORM_IP>/portrait/diags` and see whether the log download page asks for a login. If yes, which account, and can we get a separate service account for the tool? Also confirm a pod in the cluster can reach that address, because the tool runs there, not on the laptop.
- [ ] Confirm which environment the final demo runs in (lab cluster with internet, or offline). If offline, Box mode cannot be used and Import mode is the main path.
- [ ] Find out about other ways logs are obtained (shared drive, SFTP, direct device pull, manual upload). Add a row to the inventory for any source that needs a credential.
- [ ] UMF RTI: not present on the test bench as far as we know. Ask whoever owns it whether it needs a login or certificates when run in the cluster. Add the files to the inventory if so.
- [ ] Wire `install_log_redaction()` into app startup and load `SESSION_SIGNING_KEY` through `load_secret` (not done yet).
