# clipbench tasks. Run `make setup` first.
PROVIDERS ?= all
TIER ?= verified
RUN ?= latest

.PHONY: setup build-data sample fetch-audio review dry-run run score errors report publish-check test lint

setup:
	uv sync --all-extras

build-data:
	uv run clipbench data fetch-info
	uv run clipbench data build

sample:
	uv run clipbench data sample --n 150 --seed 7

fetch-audio:
	uv run clipbench data fetch-audio

review:
	uv run clipbench review

dry-run:
	uv run clipbench run --providers $(PROVIDERS) --tier $(TIER) --dry-run

run:
	uv run clipbench run --providers $(PROVIDERS) --tier $(TIER)

score:
	uv run clipbench score $(RUN)

errors:
	uv run clipbench errors $(RUN)

report: score errors
	uv run clipbench report $(RUN)
	uv run clipbench publish --check

publish-check:
	uv run clipbench publish --check

lint:
	uv run ruff check .

test:
	uv run pytest -q
