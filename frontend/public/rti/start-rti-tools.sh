#!/usr/bin/env bash
# Portrait IQ -- start GE's RTI tools (Admin Console + Monitor) on this laptop
# (SCRUM-123). The tools are GUI-only, so the field engineer runs them here,
# exports the files, and uploads them in Portrait IQ (UMF RTI > Import a GUI
# export). This script applies the settings needed to reach the UMF system
# through the Axone router's Real-Time WAN ports, so nothing has to be set by hand.
#
# Usage:   bash start-rti-tools.sh            start (or restart) the tools
#          bash start-rti-tools.sh stop       stop and remove them
#
# Needs:   Docker running and the laptop on the system's network. GE's RTI
#          tools image is already on field engineers' laptops.
#
# Optional settings (environment variables):
#   RTI_IMAGE       image name                 (default axone-umf-rtitools:router-certs)
#   RTI_ROUTER      MiniEdge / router address  (default 192.168.0.3)
#   RTI_LAPTOP_IP   this laptop's address on that network (default: found automatically)
#   RTI_OUT         folder the exports are saved to  (default ~/rti-out)
#   RTI_VNC_PORT    local port for the desktop (default 5901)
#   RTI_WAN_PORT    first of three local UDP ports for Real-Time WAN (default 32107)
#   RTI_JAVA_MEM    Java heap limit for the tools (default 3g)
set -euo pipefail

IMAGE="${RTI_IMAGE:-axone-umf-rtitools:router-certs}"
ROUTER="${RTI_ROUTER:-192.168.0.3}"
OUT="${RTI_OUT:-$HOME/rti-out}"
VNC_PORT="${RTI_VNC_PORT:-5901}"
WAN_PORT="${RTI_WAN_PORT:-32107}"
JAVA_MEM="${RTI_JAVA_MEM:-3g}"
NAME="${RTI_CONTAINER:-portrait-iq-rti-tools}"

die() { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "Docker is not installed or not on PATH."
docker info >/dev/null 2>&1 || die "Docker is not running. Start Docker and try again."

if [ "${1:-}" = "stop" ]; then
  docker rm -f "$NAME" >/dev/null 2>&1 && echo "Stopped the RTI tools." || echo "The RTI tools were not running."
  exit 0
fi

docker image inspect "$IMAGE" >/dev/null 2>&1 \
  || die "Docker on this laptop has no image named $IMAGE. If the RTI tools image has another name here, set RTI_IMAGE to it."

# This laptop's address on the network that reaches the router.
LAPTOP_IP="${RTI_LAPTOP_IP:-}"
if [ -z "$LAPTOP_IP" ]; then
  if [ "$(uname)" = "Darwin" ]; then
    IFACE=$(route -n get "$ROUTER" 2>/dev/null | awk '/interface:/{print $2}')
    [ -n "$IFACE" ] && LAPTOP_IP=$(ipconfig getifaddr "$IFACE" 2>/dev/null || true)
  else
    LAPTOP_IP=$(ip route get "$ROUTER" 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}' | head -1)
  fi
fi
[ -n "$LAPTOP_IP" ] || die "Could not find this laptop's address toward $ROUTER. Connect to the system's network, or set RTI_LAPTOP_IP."

ping -c 1 -W 2 "$ROUTER" >/dev/null 2>&1 || echo "WARNING: $ROUTER did not answer ping; the tools may not find the system."

mkdir -p "$OUT"
# (tr is cut off by head on purpose; "|| true" keeps pipefail from stopping here)
VNC_PASSWORD=$(LC_ALL=C tr -dc 'A-HJ-NP-Za-km-z2-9' </dev/urandom 2>/dev/null | head -c 8 || true)
[ ${#VNC_PASSWORD} -eq 8 ] || die "Could not generate a VNC password."
P1=$WAN_PORT; P2=$((WAN_PORT + 6)); P3=$((WAN_PORT + 8))   # logger / source pod / view pod

# Runs as root inside the container before the desktop starts. Settings go
# into this container only; the image is not changed.
SETUP='set -e
RTI=$(readlink -f /opt/rti_connext_dds)
CMD=/opt/rti_connext_dds/bin/geRunRTICommand.sh
PEERS="udpv4_wan://$ROUTER:32007,udpv4_wan://$ROUTER:32013,udpv4_wan://$ROUTER:32015"
# Real-Time WAN: this laptop is the public address; the router WAN ports are the peers.
sed -i -e "s|^export DDS_RTWAN_SERVER_PUBLIC_ADDRESS=.*|export DDS_RTWAN_SERVER_PUBLIC_ADDRESS=\"$LAPTOP_IP\"|" \
       -e "s|^export DDS_RTWAN_SERVER_PORT_LOGGER=.*|export DDS_RTWAN_SERVER_PORT_LOGGER=\"$P1\"|" \
       -e "s|^export DDS_RTWAN_SERVER_PORT_SOURCEPOD=.*|export DDS_RTWAN_SERVER_PORT_SOURCEPOD=\"$P2\"|" \
       -e "s|^export DDS_RTWAN_SERVER_PORT_VIEWPOD=.*|export DDS_RTWAN_SERVER_PORT_VIEWPOD=\"$P3\"|" \
       -e "s|^export _JAVA_OPTIONS=.*|export _JAVA_OPTIONS=\"-Xmx$JAVA_MEM -Xms512m\"|" "$CMD"
grep -q "^export NDDS_DISCOVERY_PEERS=" "$CMD" \
  || sed -i "/^export _JAVA_OPTIONS=/a export NDDS_DISCOVERY_PEERS=\"$PEERS\"" "$CMD"
# RTI Monitor: GE ships an empty peer list, so it would never find the router.
MQ=$RTI/resource/xml/RTI_MONITOR_QOS_PROFILES.xml
if [ -f "$MQ" ] && ! grep -q "udpv4_wan://$ROUTER:32013" "$MQ"; then
  sed -i "s|<!-- GE Modified: add your initial peers here -->|&\n<element>udpv4_wan://$ROUTER:32007</element><element>udpv4_wan://$ROUTER:32013</element><element>udpv4_wan://$ROUTER:32015</element>|" "$MQ"
fi
# Admin Console: join the logging (7), source (12) and view (13) domains on start.
AC=/home/developer/rti_workspace/6.1.2/user_config/admin_console/.metadata/.plugins/org.eclipse.core.runtime/.settings
mkdir -p "$AC"; P="$AC/com.rti.tools.console.entitymodel.DdsDomainManager.prefs"
{ [ -f "$P" ] && grep -v "^domains/" "$P" || printf "eclipse.preferences.version=1\nqos/library=AxoneNetQoSLibrary\nqos/profile=umfgeneral_qos\nqosFiles/count=1\nqosFiles/file0=/opt/axone-tools/qos/MDH-tools-RTIAdminConsole.xml\n"
  i=0; for d in 12 13 7; do
    printf "domains/%s/autoJoin=true\ndomains/%s/domainId=%s\ndomains/%s/qosLibrary=AxoneNetQoSLibrary\ndomains/%s/qosProfile=umfgeneral_qos\n" $i $i $d $i $i
    i=$((i + 1)); done
  echo "domains/count=3"; } > /tmp/prefs && mv /tmp/prefs "$P"
# Answer Admin Console first-run questions in advance: auto-join domains; do not
# pop up "Errors have occurred" (the tools identity is listen-only, so the
# service-admin command writer Admin Console tries to create is refused -- this
# does not affect discovery or the export); start the export wizard at the shared folder.
printf "eclipse.preferences.version=1\nstartupAutoJoin=true\n" > "$AC/com.rti.tools.console.dialogs.AutoJoinStartUpDialog.prefs"
printf "eclipse.preferences.version=1\nhideMessage=true\n" > "$AC/com.rti.tools.console.util.LogMonitor.prefs"
printf "eclipse.preferences.version=1\nfile=/home/developer/rti-out/discovery-data.xml\n" > "$AC/com.rti.tools.console.wizards.discoverydata.ExportDiscoveryDataWizard.prefs"
# Quiet the base desktop: no Ubuntu update/crash pop-ups, no screen lock.
rm -f /etc/xdg/autostart/update-notifier.desktop /etc/xdg/autostart/light-locker.desktop \
      /etc/xdg/autostart/gnome-screensaver.desktop /var/crash/*
[ -f /etc/default/apport ] && sed -i "s/^enabled=1/enabled=0/" /etc/default/apport
# Desktop: a Monitor icon next to Admin Console, the export folder, a fresh VNC password.
cat > /home/developer/Desktop/launch_monitor.desktop <<EOF
[Desktop Entry]
Type=Application
Path=/opt/rti_connext_dds/bin
Exec=/opt/rti_connext_dds/bin/geRunRTICommand.sh rtimonitor
Icon=/opt/rti_connext_dds/resource/app/app_support/launcher/icons/RTI_Launcher_Dock-Icon-Unix.ico
Name=RTI Monitor
EOF
chmod +x /home/developer/Desktop/*.desktop
printf "%s\n" "$VNC_PASSWORD" | /sbin/setuser developer vncpasswd -f > /home/developer/.vnc/passwd
chmod 600 /home/developer/.vnc/passwd
chown -R developer /home/developer/rti_workspace /home/developer/Desktop /home/developer/.vnc /home/developer/rti-out
exec /sbin/my_init'

docker rm -f "$NAME" >/dev/null 2>&1 || true
echo "Starting the RTI tools (this laptop: $LAPTOP_IP, router: $ROUTER)..."
docker run -d --name "$NAME" --platform linux/amd64 --hostname rti-tools \
  -p "127.0.0.1:$VNC_PORT:5901" \
  -p "$P1:$P1/udp" -p "$P2:$P2/udp" -p "$P3:$P3/udp" \
  -v "$OUT:/home/developer/rti-out" \
  -e ROUTER="$ROUTER" -e LAPTOP_IP="$LAPTOP_IP" -e JAVA_MEM="$JAVA_MEM" \
  -e P1="$P1" -e P2="$P2" -e P3="$P3" -e VNC_PASSWORD="$VNC_PASSWORD" \
  "$IMAGE" /bin/bash -c "$SETUP" >/dev/null

# Wait for the desktop.
for _ in $(seq 60); do
  if docker exec "$NAME" sh -c 'grep -q ":170D " /proc/net/tcp' 2>/dev/null; then READY=1; break; fi
  sleep 2
done
[ "${READY:-}" = 1 ] || die "The desktop did not start. See: docker logs $NAME"

cat <<EOF

The RTI tools are running.

  Desktop:        vnc://localhost:$VNC_PORT
  VNC password:   $VNC_PASSWORD
  Exports go to:  $OUT   (inside the desktop: /home/developer/rti-out)
  Stop them:      bash start-rti-tools.sh stop

Next: follow the steps in Portrait IQ (UMF RTI > Import a GUI export).
EOF
if [ "$(uname)" = "Darwin" ]; then open "vnc://localhost:$VNC_PORT" || true; fi
