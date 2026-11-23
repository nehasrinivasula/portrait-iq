"""
SCRUM-105: Prometheus TSDB export -- a full copy of the Prometheus database.

The database cannot be pulled over HTTP (the admin snapshot API is off and
serves no files) nor from a least-privilege pod (the data PVC is in another
namespace). It is, however, a directory of immutable blocks on the node's
disk. So this collector runs ONE short-lived, root pod that:

  * reads the Prometheus data dir over a read-only hostPath (root, to read
    files owned by Prometheus's uid),
  * `tar`s + compresses it, nice/ionice-throttled so a multi-GB write does
    not starve etcd on this shared disk, and
  * writes the archive straight onto the backend's own data volume -- the pod
    mounts the same PVC, so the file lands exactly where the backend already
    reads run artifacts. Nothing streams through the base64-exec path, which
    is for small check output only.

The archive is a native, loadable TSDB: extract it and point a Prometheus at
`prometheus-db` and it queries exactly like the source. It is binary and
carries raw metric label sets, so it cannot be masked -- packaging (Hudson)
carries it flagged raw/unmasked, the scanner (Neha) accounts for it.

This is deliberately the one plugin that runs a privileged, host-reading pod;
every other plugin stays least-privilege. Off unless PROMETHEUS_TSDB_HOSTPATH
is set (like GRAFANA_BASE_URL gates the Grafana tool).
"""
import re
import shlex

from app.collectors import k8s
from app.collectors.base import Collector, CollectorResult, PluginConfigError, PluginError
from app.config import settings

# compressor -> (file extension, shell command reading stdin -> stdout).
# {level} is filled from settings; both tools ship in the netshoot image.
_COMPRESSORS = {
    "zstd": ("zst", "zstd -T0 -{level} -q"),
    "gzip": ("gz", "gzip -{level} -c"),
}

# A scope is "full" (the whole database) or a window like "7d" / "24h": the
# blocks within that window plus the live head, still a loadable native TSDB.
_WINDOW = re.compile(r"^(\d+)([dh])$")


def _window_minutes(scope: str) -> int:
    """0 for a full export, otherwise the window size in minutes."""
    m = _WINDOW.match(scope)
    if not m:
        return 0
    n, unit = int(m.group(1)), m.group(2)
    return n * (1440 if unit == "d" else 60)


# The export runs in the pod's shell. Every interpolated value is our own
# config or a validated option (a quoted path, ints, a validated compressor) --
# never free user input. For a full export the selection is the whole
# prometheus-db dir; for a window it is the live head (wal, chunks_head) plus
# the block dirs modified within the window, so the archive is still a loadable
# TSDB for that period. The disk guard measures the SELECTION, not the whole
# database. `set -e` with an un-piped `mv` fails the step on a real error; the
# benign "file changed as we read it" warning on the live WAL does not (that is
# tar's stderr and the pipeline's exit is the compressor's).
_EXPORT_SH = r"""
set -e
SRC=/src
DB="$SRC/prometheus-db"
[ -d "$DB" ] || DB="$SRC"
OUT=__OUT__
TMP="$OUT.part"
WIN=__WIN_MIN__
mkdir -p "$(dirname "$OUT")"
rm -f "$TMP"
if [ "$WIN" -gt 0 ]; then
  BLOCKS=$(find "$DB" -maxdepth 1 -type d -name '01*' -mmin -"$WIN" 2>/dev/null)
  SEL="$DB/wal $DB/chunks_head $BLOCKS"
else
  SEL="$DB"
fi
NEED=$(du -sck $SEL 2>/dev/null | tail -1 | awk '{print $1}')
AVAIL=$(df -Pk "$(dirname "$OUT")" | awk 'NR==2{print $4}')
if [ -z "$NEED" ] || [ "$AVAIL" -lt "$NEED" ]; then
  echo "disk guard: need ${NEED}KB, only ${AVAIL}KB free on the data volume" >&2
  exit 3
fi
if [ "$NEED" -gt __CAP_KB__ ]; then
  echo "over cap: selection is ${NEED}KB, limit is __CAP_KB__KB" >&2
  exit 4
fi
REL=$(for p in $SEL; do printf '%s ' "${p#$SRC/}"; done)
ION=""
command -v ionice >/dev/null 2>&1 && ION="ionice -c3"
nice -n 19 $ION tar -C "$SRC" -cf - $REL | __COMP__ > "$TMP"
mv "$TMP" "$OUT"
echo "exported $(du -h "$OUT" 2>/dev/null | cut -f1)"
"""


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


class PrometheusTsdbCollector(Collector):
    name = "prometheus_tsdb"
    display_name = "Prometheus TSDB export"
    description = ("Full copy of the Prometheus time-series database as a "
                  "compressed, loadable native-TSDB archive, read from the node.")

    def __init__(self, limits):
        super().__init__(limits)
        self._runner = None
        self._pod: str | None = None

    def configure(self, options):
        # The engineer picks the range per run (UI); the setting is the default.
        opts = dict(options or {})
        scope = str(opts.pop("scope", settings.prometheus_tsdb_scope))
        if opts:
            raise PluginConfigError(f"{self.name} got unexpected options: {sorted(opts)}")
        if not settings.prometheus_tsdb_hostpath:
            raise PluginConfigError(
                "Prometheus TSDB export can't run: PROMETHEUS_TSDB_HOSTPATH is not set.")
        if scope != "full" and not _WINDOW.match(scope):
            raise PluginConfigError(
                f"prometheus_tsdb scope must be 'full' or a window like '7d' or '24h'; got {scope!r}.")
        if settings.prometheus_tsdb_compressor not in _COMPRESSORS:
            raise PluginConfigError(
                f"prometheus_tsdb compressor must be one of {sorted(_COMPRESSORS)}; "
                f"got {settings.prometheus_tsdb_compressor!r}.")
        self.options = {"scope": scope}

    def audit_fields(self):
        # A privileged, host-reading pod that exports an unmasked database is
        # privacy-relevant: record that it ran, the range, and against what.
        return {
            "prometheus_tsdb_scope": self.options.get("scope", settings.prometheus_tsdb_scope),
            "prometheus_tsdb_hostpath": settings.prometheus_tsdb_hostpath,
        }

    def _make_runner(self):
        return k8s.get_pod_runner()

    def _export_script(self, out_path: str, cap_kb: int, comp_cmd: str, win_min: int) -> str:
        return (_EXPORT_SH
                .replace("__OUT__", shlex.quote(out_path))
                .replace("__CAP_KB__", str(cap_kb))
                .replace("__WIN_MIN__", str(win_min))
                .replace("__COMP__", comp_cmd))

    def run(self, target_ip: str) -> CollectorResult:
        out_dir = self.require_output_dir()
        if self._pod:                      # run() again after a partial failure: start clean
            self.cleanup()

        scope = self.options["scope"]
        win_min = _window_minutes(scope)
        ext, comp_tmpl = _COMPRESSORS[settings.prometheus_tsdb_compressor]
        comp_cmd = comp_tmpl.format(level=settings.prometheus_tsdb_level)
        artifact = out_dir / f"prometheus_tsdb.tar.{ext}"
        cap_kb = settings.prometheus_tsdb_max_output_mb * 1024
        timeout = settings.prometheus_tsdb_timeout_seconds

        self._runner = self._make_runner()
        self._pod = k8s.pod_name(self.name, self.job_id)    # set first, so cleanup() finds it
        self._runner.create(
            name=self._pod, tool=self.name, job_id=self.job_id,
            image=settings.prometheus_tsdb_image or settings.netshoot_image,
            command=["sleep", str(timeout + 120)],
            deadline_s=timeout + 180, limits=self.limits,
            run_as_user=0,
            # root alone cannot write the archive into the backend-owned (uid
            # 10001) output dir once ALL caps are dropped; DAC_OVERRIDE lets it
            # read any source file and write the archive there, nothing more.
            add_capabilities=("DAC_OVERRIDE",),
            extra_volumes=(
                {"name": "tsdb-src", "hostPath": {
                    "path": settings.prometheus_tsdb_hostpath, "type": "Directory"}},
                {"name": "tsdb-out", "persistentVolumeClaim": {
                    "claimName": settings.prometheus_tsdb_data_pvc}},
            ),
            extra_mounts=(
                {"name": "tsdb-src", "mountPath": "/src", "readOnly": True},
                {"name": "tsdb-out", "mountPath": settings.prometheus_tsdb_data_mount},
            ),
        )
        self._runner.wait_running(self._pod, settings.tool_pod_start_timeout_seconds)

        script = self._export_script(str(artifact), cap_kb, comp_cmd, win_min)
        res = self._runner.exec(self._pod, ["sh", "-c", script], timeout)
        if res.exit_code != 0:
            reason = {3: "The Prometheus data volume did not have room for the export.",
                      4: "The Prometheus database is larger than the configured export limit."}.get(
                          res.exit_code, "Could not export the Prometheus database.")
            raise PluginError(
                f"prometheus_tsdb export failed (exit {res.exit_code}): {res.stderr[-500:]}",
                safe_reason=reason)

        # The pod wrote to the backend's own PVC, so the file is here now.
        if not artifact.exists() or artifact.stat().st_size == 0:
            raise PluginError(
                f"prometheus_tsdb produced no archive at {artifact}",
                safe_reason="The Prometheus export produced no file.")
        size = artifact.stat().st_size
        span = "full database" if scope == "full" else f"last {scope}"
        return CollectorResult(
            tool=self.name,
            artifact_paths=[str(artifact)],
            summary=f"Prometheus TSDB exported ({span}): {_human(size)} "
                    f"({settings.prometheus_tsdb_compressor}).",
            warnings=["Database export: binary and unmasked -- it carries raw metric "
                      "label sets, so it is exported as-is, not PHI/PII-masked."],
        )

    def cleanup(self) -> None:
        if self._pod and self._runner:
            self._runner.delete(self._pod)
        self._pod = None
