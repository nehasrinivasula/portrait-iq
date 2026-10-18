# Portrait IQ: local development and lab deployment (SCRUM-86).
#
#   make help          list targets
#
# Lab settings (node address, CPU type, namespace...) go in deploy/lab.mk,
# which is gitignored: copy deploy/lab.mk.example and fill it in. Anything can
# also be passed on the command line, e.g. `make deploy NAMESPACE=x`.
#
# Lab targets run commands on the MiniEdge over SSH, as root via sudo, which
# is what the lab notes require before kubectl. You type the SSH password once
# per 10 minutes (the connection is reused); sudo may also ask for it.

-include deploy/lab.mk

# --- Settings -------------------------------------------------------------

PYTHON        ?= python3
VENV          ?= backend/.venv

NODE          ?= gehc@192.168.0.3
REMOTE_SUDO   ?= sudo -i
REMOTE_DIR    ?= /tmp/portrait-iq-deploy

# Unknown until `make api-check` has run on the lab; no guesses here.
PLATFORM      ?=#   linux/amd64 or linux/arm64 (the node's CPU type)
IMPORT_CMD    ?=#   e.g. k3s ctr images import -   (depends on the runtime)
LOG_EXPORT_NAMESPACE ?=#   namespace of axone-log-export-tcp

NAMESPACE     ?= portrait-iq
NODE_PORT     ?= 30080
LOG_EXPORT_URL ?= http://axone-log-export-tcp.$(LOG_EXPORT_NAMESPACE).svc.cluster.local:9200
SECRETS_DIR   ?=#   folder OUTSIDE the repo, one file per secret (backend/SECRETS.md)
API_CHECK_HOUR ?=#  optional, YYYY-MM-DDTHH; default is the last full hour on the node

IMAGE_NAME    ?= portrait-iq
TAG           := $(shell git rev-parse --short HEAD 2>/dev/null)$(shell git diff --quiet HEAD -- 2>/dev/null || echo -dirty)
BUILD_DIR     := dist
BUNDLE_DIR    := $(BUILD_DIR)/remote

SSH_OPTS      ?= -o ControlMaster=auto -o ControlPath=$(HOME)/.ssh/portrait-iq-%C -o ControlPersist=10m
SSH           := ssh $(SSH_OPTS)
SCP           := scp $(SSH_OPTS)
REMOTE        := $(SSH) -t $(NODE) $(REMOTE_SUDO) sh $(REMOTE_DIR)/remote.sh

require = $(if $(strip $($(1))),,$(error $(1) is not set. Add it to deploy/lab.mk (see deploy/lab.mk.example) or pass $(1)=...))

.DEFAULT_GOAL := help
.PHONY: help setup test dev image image-save image-load secrets deploy pod-check \
        status logs api-check undeploy clean render bundle

help:  ## List targets
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

# --- Local ----------------------------------------------------------------

setup:  ## Create the Python venv, install dependencies and git hooks
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q -r backend/requirements.txt
	@if [ -f .pre-commit-config.yaml ]; then \
		$(VENV)/bin/pip install -q pre-commit && $(VENV)/bin/pre-commit install; fi
	@if [ -f frontend/package.json ]; then cd frontend && npm ci; fi

test:  ## Run backend (and frontend, if present) tests
	cd backend && ../$(VENV)/bin/python -m pytest -q
	@if [ -f frontend/package.json ]; then cd frontend && npm test; fi

dev:  ## Run the backend locally with auto-reload (http://localhost:8000/docs)
	cd backend && ../$(VENV)/bin/uvicorn app.main:app --reload

# --- Image ----------------------------------------------------------------

image:  ## Build the image for the MiniEdge's CPU type
	$(call require,PLATFORM)
	docker buildx build --platform $(PLATFORM) -t $(IMAGE_NAME):$(TAG) --load backend
	@mkdir -p $(BUILD_DIR) && echo $(IMAGE_NAME):$(TAG) > $(BUILD_DIR)/image.ref
	@echo "Built $(IMAGE_NAME):$(TAG) for $(PLATFORM)"

image-save: image  ## Save the image to dist/images/ for copying to the lab
	@mkdir -p $(BUILD_DIR)/images && rm -f $(BUILD_DIR)/images/$(IMAGE_NAME)-*.tar.gz
	docker save $(IMAGE_NAME):$(TAG) | gzip > $(BUILD_DIR)/images/$(IMAGE_NAME)-$(TAG).tar.gz
	@ls -lh $(BUILD_DIR)/images/

# Loads EVERY .tar.gz in dist/images/, so plugin images (SCRUM-39) only need
# to be saved there; this target does not change.
image-load:  ## Copy dist/images/*.tar.gz to the node and import them
	$(call require,IMPORT_CMD)
	@$(MAKE) --no-print-directory bundle
	@ls $(BUILD_DIR)/images/*.tar.gz >/dev/null 2>&1 || { echo "No images in $(BUILD_DIR)/images; run make image-save"; exit 1; }
	$(SSH) $(NODE) mkdir -p -m 700 $(REMOTE_DIR)/images
	$(SCP) $(BUILD_DIR)/images/*.tar.gz $(NODE):$(REMOTE_DIR)/images/
	$(REMOTE) load-images

# --- Lab ------------------------------------------------------------------

# render: fill in the manifests and write config.env locally (no SSH).
# bundle: render, then copy everything to REMOTE_DIR on the node.
render:
	@rm -rf $(BUNDLE_DIR) && mkdir -p $(BUNDLE_DIR)/k8s
	@cp deploy/scripts/remote.sh $(BUNDLE_DIR)/
	@printf "NAMESPACE='%s'\nIMPORT_CMD='%s'\nLOG_EXPORT_URL='%s'\nAPI_CHECK_HOUR='%s'\n" \
		"$(NAMESPACE)" "$(IMPORT_CMD)" "$(LOG_EXPORT_URL)" "$(API_CHECK_HOUR)" > $(BUNDLE_DIR)/config.env
	@image=$$(cat $(BUILD_DIR)/image.ref 2>/dev/null || echo "$(IMAGE_NAME):unbuilt"); \
	for f in deploy/k8s/*.yaml; do \
		sed -e "s|__IMAGE__|$$image|" -e "s|__LOG_EXPORT_URL__|$(LOG_EXPORT_URL)|" \
		    -e "s|__NODE_PORT__|$(NODE_PORT)|" "$$f" > $(BUNDLE_DIR)/k8s/$$(basename $$f); \
	done

bundle: render
	$(SSH) $(NODE) mkdir -p -m 700 $(REMOTE_DIR)
	$(SCP) -r $(BUNDLE_DIR)/. $(NODE):$(REMOTE_DIR)/

api-check: bundle  ## Read-only: find the Log Export API, pull one hour of CSV into dist/api-check/
	$(REMOTE) api-check
	@mkdir -p $(BUILD_DIR)/api-check
	$(SCP) $(NODE):$(REMOTE_DIR)/api-check.csv $(BUILD_DIR)/api-check/
	@echo "Saved $(BUILD_DIR)/api-check/api-check.csv. Real lab data: never commit it."

secrets:  ## Create/update the Kubernetes Secret from SECRETS_DIR (outside the repo)
	$(call require,SECRETS_DIR)
	@dir=$$(cd "$(SECRETS_DIR)" 2>/dev/null && pwd) || { echo "SECRETS_DIR $(SECRETS_DIR) not found"; exit 1; }; \
	case "$$dir/" in "$(CURDIR)/"*) echo "SECRETS_DIR must be outside the repo"; exit 1;; esac; \
	loose=$$(find "$$dir" -type f -perm -004); \
	[ -z "$$loose" ] || { echo "Readable by everyone, run chmod 600: $$loose"; exit 1; }
	@$(MAKE) --no-print-directory bundle
	$(SCP) -rp "$(SECRETS_DIR)" $(NODE):$(REMOTE_DIR)/secrets
	$(REMOTE) secrets

deploy:  ## Create the namespace if needed, apply manifests, wait for the pod
	$(call require,LOG_EXPORT_NAMESPACE)
	@test -f $(BUILD_DIR)/image.ref || { echo "No image built yet; run make image-save image-load"; exit 1; }
	@$(MAKE) --no-print-directory bundle
	$(REMOTE) deploy
	@echo "Open http://$(lastword $(subst @, ,$(NODE))):$(NODE_PORT)/docs from a laptop on the lab switch."

pod-check:  ## From inside the pod, check the Log Export API answers
	$(call require,LOG_EXPORT_NAMESPACE)
	@$(MAKE) --no-print-directory bundle
	$(REMOTE) pod-check

status: bundle  ## Show our pod, service, volume and recent events
	$(REMOTE) status

logs: bundle  ## Follow the pod's logs (Ctrl-C to stop)
	$(REMOTE) logs

undeploy: bundle  ## Remove everything make deploy created (deletes the database volume)
	$(REMOTE) undeploy
	$(SSH) $(NODE) rm -rf $(REMOTE_DIR)

clean:  ## Remove local build output (dist/)
	rm -rf $(BUILD_DIR)
