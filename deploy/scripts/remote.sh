#!/bin/sh
# Runs ON the MiniEdge, as root (the Makefile calls it through `sudo -i`).
# The Makefile copies this file, config.env and the rendered manifests into
# REMOTE_DIR first; do not run it from your laptop.
#
#   remote.sh api-check     read-only: find the Log Export service, pull one hour of CSV
#   remote.sh load-images   import every images/*.tar.gz into the node's container runtime
#   remote.sh secrets       create or update the portrait-iq-secrets Secret from secrets/
#   remote.sh deploy        create our namespace if needed, apply manifests, wait for the pod
#   remote.sh pod-check     from inside our pod, check the Log Export API answers
#   remote.sh status        pods, service, volume, recent events
#   remote.sh logs          follow the pod's logs
#   remote.sh undeploy      remove everything labelled portrait-iq (and our namespace)
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/config.env"   # NAMESPACE, IMPORT_CMD, TAG_CMD, IMAGE_ALIAS_PREFIX, LOG_EXPORT_URL, API_CHECK_HOUR

LABEL="app.kubernetes.io/name=portrait-iq"
MANAGED="app.kubernetes.io/managed-by"
SERVICE="${LOG_EXPORT_SERVICE:-axone-log-export-tcp}"

die() { echo "ERROR: $*" >&2; exit 1; }
k() { kubectl -n "$NAMESPACE" "$@"; }

api_check() {
    command -v curl >/dev/null || die "curl is not installed on this node"
    command -v kubectl >/dev/null || die "kubectl is not on root's PATH under sudo -i (try REMOTE_SUDO=sudo in deploy/lab.mk)"
    svcs=$(kubectl get svc -A --no-headers 2>&1) || die "kubectl failed: $svcs"
    ns=$(printf '%s\n' "$svcs" | awk -v s="$SERVICE" '$2 == s {print $1; exit}')
    if [ -z "$ns" ]; then
        echo "No service named $SERVICE. Services that look related (namespace, name, type, ports):" >&2
        printf '%s\n' "$svcs" | grep -iE 'log|axone|export' | awk '{print "  " $1, $2, $3, $6}' >&2 \
            || echo "  none; all services: $(printf '%s\n' "$svcs" | awk '{print $1 "/" $2}' | tr '\n' ' ')" >&2
        die "set LOG_EXPORT_SERVICE=<name> in deploy/lab.mk if one of these is it"
    fi

    echo "== Log Export service namespace: $ns   (set LOG_EXPORT_NAMESPACE=$ns)"
    echo "== Node (CPU type -> PLATFORM, runtime -> IMPORT_CMD):"
    kubectl get nodes -o wide
    kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}: arch={.status.nodeInfo.architecture} runtime={.status.nodeInfo.containerRuntimeVersion}{"\n"}{end}'
    echo "== Node clock: local $(date '+%Y-%m-%d %H:%M:%S %Z')   UTC $(date -u '+%Y-%m-%d %H:%M:%S')"

    hour=${API_CHECK_HOUR:-$(date -d '1 hour ago' +%Y-%m-%dT%H 2>/dev/null || true)}
    [ -n "$hour" ] || die "could not compute last hour; pass API_CHECK_HOUR=YYYY-MM-DDTHH"
    start="$hour:00:00.000"
    end="$hour:59:59.999"   # same HH on both ends: the API requires it

    port=19200
    kubectl -n "$ns" port-forward "svc/$SERVICE" "$port:9200" >"$HERE/port-forward.log" 2>&1 &
    pf=$!
    trap 'kill $pf 2>/dev/null || true' EXIT
    i=0
    until curl -s -o /dev/null "http://127.0.0.1:$port/" 2>/dev/null; do
        i=$((i + 1)); [ $i -le 20 ] || die "port-forward did not come up: $(cat "$HERE/port-forward.log")"
        sleep 0.5
    done

    base="http://127.0.0.1:$port"
    out="$HERE/api-check.csv"
    echo "== GET /getAllDeviceLogsInCSV  $start -> $end"
    code=$(curl -sS -o "$out" -w '%{http_code}' \
        "$base/getAllDeviceLogsInCSV?startTimestamp=$start&endTimestamp=$end")
    echo "HTTP $code   (no login was sent: 200 means the API needs none)"
    if [ "$code" != 200 ]; then
        echo "Body:"; head -c 500 "$out"; echo; return 1
    fi
    rows=$(($(wc -l <"$out") - 1))
    echo "Rows: $rows"
    echo "Header: $(head -n 1 "$out")"
    if [ "$rows" -gt 0 ]; then
        echo "First timestamp: $(sed -n 2p "$out" | cut -d, -f1)   (compare with the hour asked for)"
        echo "Last timestamp:  $(tail -n 1 "$out" | cut -d, -f1)"
    fi

    echo "== eventCategory codes (approximate: reads column 10 of each response)"
    for c in 0 1 2 3 4 5 6 7 8; do
        body=$(curl -s -w '\n%{http_code}' \
            "$base/getLogsByDateAndEventInCsv?eventCategory=$c&startTimestamp=$start&endTimestamp=$end")
        status=$(printf '%s\n' "$body" | tail -n 1)
        names=$(printf '%s\n' "$body" | sed '1d;$d' | cut -d, -f10 | tr -d '"' | sort -u | tr '\n' ' ')
        echo "  $c -> HTTP $status ${names:-(no rows in this hour)}"
    done
}

load_images() {
    [ -n "${IMPORT_CMD:-}" ] || die "IMPORT_CMD is not set"
    found=0
    for f in "$HERE"/images/*.tar.gz; do
        [ -e "$f" ] || continue
        found=1
        echo "== importing $(basename "$f")"
        out=$(gunzip -c "$f" | sh -c "$IMPORT_CMD") || die "importing $(basename "$f") failed: $out"
        printf '%s\n' "$out"
        alias_images "$out"
    done
    [ $found = 1 ] || die "no images in $HERE/images"
    rm -rf "$HERE/images"
}

# A Palette webhook on the bench rewrites pod image names to its registry
# (docker.io/library/portrait-iq:TAG -> PREFIX/library/portrait-iq:TAG). Pods
# never pull, so give each imported image that second name too.
alias_images() {
    [ -n "${IMAGE_ALIAS_PREFIX:-}" ] || return 0
    printf '%s\n' "$1" | grep -oE 'docker\.io/[^ (@]+:[^ (@]+' | sort -u | while read -r ref; do
        alias="$IMAGE_ALIAS_PREFIX/${ref#docker.io/}"
        sh -c "$TAG_CMD \"$ref\" \"$alias\"" >/dev/null || die "could not tag $ref as $alias"
        echo "== also named $alias"
    done
}

secrets() {
    dir="$HERE/secrets"
    [ -d "$dir" ] || die "no secrets directory was copied"
    trap 'rm -rf "$dir"' EXIT   # never leave secret files behind on the node
    kubectl get ns "$NAMESPACE" >/dev/null 2>&1 || die "namespace $NAMESPACE does not exist yet; run make deploy first"
    k create secret generic portrait-iq-secrets --from-file="$dir" --dry-run=client -o yaml \
        | kubectl label --local -f - "$LABEL" -o yaml \
        | k apply -f -
    if k get deployment portrait-iq >/dev/null 2>&1; then
        k rollout restart deployment/portrait-iq
    fi
}

deploy() {
    if ! kubectl get ns "$NAMESPACE" >/dev/null 2>&1; then
        kubectl create namespace "$NAMESPACE"
        # Marks the namespace as ours, so undeploy may delete it. A namespace
        # without this label (e.g. Portrait's own) is never deleted.
        kubectl label namespace "$NAMESPACE" "$MANAGED=portrait-iq-make"
    fi
    k apply -f "$HERE/k8s/"
    if ! k rollout status deployment/portrait-iq --timeout=180s; then
        echo "Pod did not become ready. Volume and events:" >&2
        k get pvc,pods -l "$LABEL" >&2
        k get events --sort-by=.lastTimestamp | tail -n 15 >&2
        exit 1
    fi
}

pod_check() {
    # A request with no parameters should come back 400: that proves the pod
    # can reach the API without pulling any logs.
    k exec deploy/portrait-iq -- python3 -c "
import sys, urllib.request, urllib.error
url = '$LOG_EXPORT_URL/getAllDeviceLogsInCSV'
try:
    urllib.request.urlopen(url, timeout=10)
    print('reachable: HTTP 200 from', url)
except urllib.error.HTTPError as e:
    print('reachable: HTTP', e.code, 'from', url)
except Exception as e:
    print('NOT reachable:', url, '-', e)
    sys.exit(1)
"
}

status() {
    k get deploy,pods,svc,pvc,secret -l "$LABEL" -o wide
    echo "== recent events"
    k get events --sort-by=.lastTimestamp 2>/dev/null | tail -n 10
}

logs() { k logs -f deploy/portrait-iq --tail=200; }

undeploy() {
    k delete deployment,service,pvc,secret -l "$LABEL" --ignore-not-found --wait=true
    owner=$(kubectl get ns "$NAMESPACE" -o jsonpath="{.metadata.labels.app\.kubernetes\.io/managed-by}" 2>/dev/null || true)
    if [ "$owner" = portrait-iq-make ]; then
        kubectl delete namespace "$NAMESPACE"
    else
        echo "Namespace $NAMESPACE was not created by make deploy; leaving it."
    fi
    echo "Imported images stay in the container runtime; remove them with the runtime's own command if needed."
}

case "${1:-}" in
    api-check) api_check ;;
    load-images) load_images ;;
    secrets) secrets ;;
    deploy) deploy ;;
    pod-check) pod_check ;;
    status) status ;;
    logs) logs ;;
    undeploy) undeploy ;;
    *) die "unknown command '${1:-}'" ;;
esac
