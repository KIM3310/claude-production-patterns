.PHONY: install test lint format pattern

install:
	pip install -e ".[dev]"

test:
	pytest --tb=short

lint:
	ruff check patterns
	mypy patterns --ignore-missing-imports

format:
	black patterns
	ruff check --fix patterns

pattern:
ifndef NAME
	@echo "Usage: make pattern NAME=01-rate-limiting"
	@exit 1
endif
	python -m patterns.$(NAME).pattern

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
