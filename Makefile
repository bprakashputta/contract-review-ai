.PHONY: install dev test lint fmt data eval eval-all qa-eval report up down

install:
	uv sync

dev:
	uv run contract-review-ai

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff format . && uv run ruff check --fix .

data:  ## download CUAD v1 (official train/test split) into data/raw
	uv run python -c "from pathlib import Path; from contract_review_ai.evaluation.dataset import download; download(Path('data'))"

eval: data  ## headline run: final pipeline on the full CUAD test split (102 contracts)
	uv run python -m contract_review_ai.evaluation.run --config pipeline --split test

eval-all: data  ## every configuration reported in the README
	uv run python -m contract_review_ai.evaluation.run --config naive --split test
	uv run python -m contract_review_ai.evaluation.run --config full --split test
	uv run python -m contract_review_ai.evaluation.run --config pipeline --split test
	uv run python -m contract_review_ai.evaluation.run --config pipeline_exact_only --split test
	uv run python -m contract_review_ai.evaluation.run --config pipeline --split test --n 40
	uv run python -m contract_review_ai.evaluation.run --config pipeline --split test --n 40 --model claude-sonnet-5-5
	$(MAKE) qa-eval

qa-eval: data
	uv run python -m contract_review_ai.evaluation.qa_eval --split test --n 30 --no-expand
	uv run python -m contract_review_ai.evaluation.qa_eval --split test --n 30

report:
	uv run python -m contract_review_ai.evaluation.run --report

up:
	docker compose up --build -d

down:
	docker compose down
