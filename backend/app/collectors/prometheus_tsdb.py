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

# The export runs in the pod's shell. Every interpolated value is from our own
# config (a quoted path, an int, a validated compressor) -- never user input.
# `set -e` plus an un-piped `mv` make a failed tar/compress fail the step; the
# benign "file changed as we read it" warning on the live WAL does not (it is
# tar's stderr and the pipeline's exit is the compressor's).
_EXPORT_SH = r"""
set -e
SRC=/src
OUT=__OUT__
TMP="$OUT.part"
mkdir -p "$(dirname "$OUT")"
rm -f "$TMP"
AVAIL=$(df -Pk "$(dirname "$OUT")" | awk 'NR==2{print $4}')
NEED=$(du -sk "$SRC" | awk '{print $1}')
if [ -z "$NEED" ] || [ "$AVAIL" -lt "$NEED" ]; then
  echo "disk guard: need ${NEED}KB, only ${AVAIL}KB free on the data volume" >&2
  exit 3
fi
if [ "$NEED" -gt __CAP_KB__ ]; then
  echo "over cap: source is ${NEED}KB, limit is __CAP_KB__KB" >&2
  exit 4
fi
ION=""
command -v ionice >/dev/null 2>&1 && ION="ionice -c3"
nice -n 19 $ION tar -C "$SRC" -cf - . | __COMP__ > "$TMP"
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
        if options:
            raise PluginConfigError(f"{self.name} takes no options; got {sorted(options)}")
        if not settings.prometheus_tsdb_hostpath:
            raise PluginConfigError(
                "Prometheus TSDB export can't run: PROMETHEUS_TSDB_HOSTPATH is not set.")
        if settings.prometheus_tsdb_scope != "full":
            raise PluginConfigError(
                f"prometheus_tsdb scope '{settings.prometheus_tsdb_scope}' is not supported yet; "
                "only 'full' is implemented.")
        if settings.prometheus_tsdb_compressor not in _COMPRESSORS:
            raise PluginConfigError(
                f"prometheus_tsdb compressor must be one of {sorted(_COMPRESSORS)}; "
                f"got {settings.prometheus_tsdb_compressor!r}.")
        self.options = {}

    def audit_fields(self):
        # A privileged, host-reading pod that exports an unmasked database is
        # privacy-relevant: record that it ran and against what.
        return {
            "prometheus_tsdb_scope": settings.prometheus_tsdb_scope,
            "prometheus_tsdb_hostpath": settings.prometheus_tsdb_hostpath,
        }

    def _make_runner(self):
        return k8s.get_pod_runner()

    def _export_script(self, out_path: str, cap_kb: int, comp_cmd: str) -> str:
        return (_EXPORT_SH
                .replace("__OUT__", shlex.quote(out_path))
                .replace("__CAP_KB__", str(cap_kb))
                .replace("__COMP__", comp_cmd))

    def run(self, target_ip: str) -> CollectorResult:
        out_dir = self.require_output_dir()
        if self._pod:                      # run() again after a partial failure: start clean
            self.cleanup()

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

        script = self._export_script(str(artifact), cap_kb, comp_cmd)
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
        return CollectorResult(
            tool=self.name,
            artifact_paths=[str(artifact)],
            summary=f"Prometheus database exported: {_human(size)} ({settings.prometheus_tsdb_compressor}).",
            warnings=["Full database export: binary and unmasked -- it carries raw metric "
                      "label sets, so it is exported as-is, not PHI/PII-masked."],
        )

    def cleanup(self) -> None:
        if self._pod and self._runner:
            self._runner.delete(self._pod)
        self._pod = None
