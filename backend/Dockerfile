# Portrait IQ backend image (SCRUM-86).
#
# Ubuntu 22.04 base, per the requirements doc's standard-image rule.
# Build it with `make image` from the repo root, which sets --platform to the
# MiniEdge's CPU type. A plain `docker build` on an Apple-silicon Mac produces
# an arm64 image that will not start on an x86 node.
#
# No secrets are baked in: they are mounted at runtime from a Kubernetes
# Secret (see deploy/k8s/deployment.yaml and backend/SECRETS.md).
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tshark/editcap read and trim captures for the tshark plugin (SCRUM-39).
# No setuid dumpcap: the backend never captures itself.
RUN echo "wireshark-common wireshark-common/install-setuid boolean false" | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
       tshark \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Non-root user; /data is the mount point for the database volume.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app \
    && mkdir /data && chown app:app /data
USER 10001

ENV DATABASE_URL=sqlite:////data/portrait_iq.db
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
