.PHONY: format lint docstring check test cov sync export export-ladybug viewer-install viewer-dev viewer-build
.DEFAULT_GOAL := sync

# Install dependencies
sync:
	uv sync --all-extras --all-groups

# Format code with ruff
format:
	uv run ruff check --select I --select E --select F --fix || true
	uv run ruff format || true

# Run unit tests
test:
	@uv run pytest tests/

# Run tests with coverage report
cov:
	@uv run pytest tests/unit/ --cov=openjiuwen_search_base --cov-report=term-missing

# Export graph for this project
export:
	uv run python -m openjiuwen_search_base.codegraph.export . -o .jiuwen_graph

# CodeGraph viewer targets
viewer: viewer-install
	npm run dev --prefix codegraph-viewer

viewer-install:
	npm install --prefix codegraph-viewer
