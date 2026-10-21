# clipbench tasks. Run `make setup` first.
PROVIDERS ?= all
TIER ?= verified
RUN ?= latest
LANG_CODE ?= ja

.PHONY: setup build-data sample fetch-audio review dry-run run score errors report publish-check test lint check-data align

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
	uv run clipbench run --providers $(PROVIDERS) --tier $(TIER) --lang $(LANG_CODE) --dry-run

run:
	uv run clipbench run --providers $(PROVIDERS) --tier $(TIER) --lang $(LANG_CODE)

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


check-data:
	uv run clipbench data check --lang $(LANG_CODE)

align:
	uv run clipbench align --lang $(LANG_CODE)
