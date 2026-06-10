DOCKERFILE ?= Dockerfile
VERSION ?= $(shell awk -F= '/^ARG SNDS_EXPORTER_VERSION=/{print $$2; exit}' $(DOCKERFILE))
IMAGE_NAME ?= snds-exporter
IMAGE_REPO ?= local/$(IMAGE_NAME)
TAG ?= $(VERSION)
CONTAINER_NAME ?= $(IMAGE_NAME)
PORT ?= 9100
LOCAL_ENV_FILE_ARG = $(if $(wildcard .local.env),--env-file .local.env,)
_ML_VERSION = v9
REPO_ROOT = $(shell git rev-parse --show-toplevel)
CHANGED_FILES = $(shell git status --porcelain -- . | awk '{print $$2}' | tr '\n' ',' | sed 's/,$$//')

define mega-linter-runner
	docker run --rm \
		-v /var/run/docker.sock:/var/run/docker.sock:rw \
		-v $(REPO_ROOT):/tmp/lint:rw \
		-e MEGALINTER_CONFIG=images/snds-exporter/.mega-linter.yml \
		$(strip $(2)) \
		oxsecurity/$(strip $(1)):$(_ML_VERSION)
endef

.PHONY: _check-for-all
_check-for-all:
	@echo "================================== Warning! ==================================" && \
	echo "== Linting the whole codebase can take" && \
	echo "== a very long time" && \
	echo "==============================================================================="
	@echo -n "Are you sure? [y/N] " && read ans && [ $${ans:-N} = y ]

.PHONY: _check-for-changed-files
_check-for-changed-files:
	@if [ -z "$(CHANGED_FILES)" ]; then \
		echo "No changed files to lint"; \
		exit 1; \
	fi

.PHONY: build
build:
	docker build --tag $(IMAGE_REPO):$(TAG) .

.PHONY: run
run:
	docker run --rm --name $(CONTAINER_NAME) -p $(PORT):9100 $(LOCAL_ENV_FILE_ARG) $(IMAGE_REPO):$(TAG)

.PHONY: shell
shell:
	docker run --rm --entrypoint /bin/sh -it $(LOCAL_ENV_FILE_ARG) $(IMAGE_REPO):$(TAG)

.PHONY: test
test:
	python3 -m unittest discover -s tests

.PHONY: verify-python
verify-python:
	python3 -m compileall rootfs/usr/local/bin/exporter.py rootfs/usr/local/bin/snds_token_helper.py tests/test_exporter.py

.PHONY: lint
lint: _check-for-changed-files
	$(call mega-linter-runner, megalinter, -e SKIP_CLI_LINT_MODES=project -e MEGALINTER_FILES_TO_LINT=$(CHANGED_FILES))

.PHONY: lint-fix
lint-fix: _check-for-changed-files
	$(call mega-linter-runner, megalinter, -e SKIP_CLI_LINT_MODES=project -e MEGALINTER_FILES_TO_LINT=$(CHANGED_FILES) -e APPLY_FIXES=all)

.PHONY: lint-ci
lint-ci: _check-for-changed-files
	$(call mega-linter-runner, megalinter-ci_light, -e SKIP_CLI_LINT_MODES=project -e MEGALINTER_FILES_TO_LINT=$(CHANGED_FILES))

.PHONY: lint-ci-fix
lint-ci-fix: _check-for-changed-files
	$(call mega-linter-runner, megalinter-ci_light, -e SKIP_CLI_LINT_MODES=project -e MEGALINTER_FILES_TO_LINT=$(CHANGED_FILES) -e APPLY_FIXES=all)

.PHONY: lint-python
lint-python: _check-for-changed-files
	$(call mega-linter-runner, megalinter-python, -e SKIP_CLI_LINT_MODES=project -e MEGALINTER_FILES_TO_LINT=$(CHANGED_FILES))

.PHONY: lint-python-fix
lint-python-fix: _check-for-changed-files
	$(call mega-linter-runner, megalinter-python, -e SKIP_CLI_LINT_MODES=project -e MEGALINTER_FILES_TO_LINT=$(CHANGED_FILES) -e APPLY_FIXES=all)

.PHONY: lint-all-files
lint-all-files: _check-for-all
	$(call mega-linter-runner, megalinter, -e SKIP_CLI_LINT_MODES=project)

.PHONY: lint-all-files-fix
lint-all-files-fix: _check-for-all
	$(call mega-linter-runner, megalinter, -e SKIP_CLI_LINT_MODES=project -e APPLY_FIXES=all)

.PHONY: lint-all
lint-all: _check-for-all
	$(call mega-linter-runner, megalinter)

.PHONY: lint-all-fix
lint-all-fix: _check-for-all
	$(call mega-linter-runner, megalinter, -e APPLY_FIXES=all)

.PHONY: check
check: verify-python test build
