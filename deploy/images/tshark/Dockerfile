# tshark tool image for live capture (SCRUM-39). Ubuntu 22.04, per the
# requirements doc's standard-image rule (D-11). The backend starts it as a
# short-lived pod with host networking and NET_RAW/NET_ADMIN only, runs
# tshark in it with exec, copies the capture out and deletes the pod.
#
# Build for the node's CPU type and import it the same way as the backend
# image (deploy/README.md), then set TSHARK_IMAGE if the tag differs from
# the default in backend/app/config.py:
#
#   docker build --platform linux/amd64 -t portrait-iq-tshark:0.1 deploy/images/tshark
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Runs as root inside the pod: capture needs the raw-socket capabilities the
# pod grants, and no setuid dumpcap is needed for that.
RUN echo "wireshark-common wireshark-common/install-setuid boolean false" | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends tshark \
    && rm -rf /var/lib/apt/lists/*

CMD ["sleep", "3600"]
