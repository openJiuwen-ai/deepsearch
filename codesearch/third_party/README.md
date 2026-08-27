# Optional third-party: ContextBench

ContextBench is **not** a git submodule and is **not** fetched by `git clone` or CI.
The product SDK, unit tests, wheels, and the pipeline do not need it.
It is **not** a `[project.optional-dependencies]` extra: ContextBench has no
packaging metadata, and a `git+` URL in extras is written into the wheel
(`Requires-Dist`) and rejected by package indexes. The existing `[bench]` extra
only covers pandas / pyarrow / tree-sitter for I/O and scoring.

Use it only when you want to run `python -m benchmarks.contextbench.runner`.

## Fetch

From `codesearch/`:

```sh
bash scripts/fetch_contextbench.sh
```

Equivalent:

```sh
git clone https://github.com/EuniAI/ContextBench third_party/contextbench
```

A China-reachable mirror (or a local copy) can replace the GitHub URL:

```sh
CONTEXTBENCH_URL=<mirror-or-local-bundle> bash scripts/fetch_contextbench.sh
# or point at an existing checkout without cloning:
export CONTEXTBENCH_DIR=/path/to/ContextBench
```

Optional pin (the last gitlink while this used to be a submodule):

```sh
CONTEXTBENCH_PIN=1436c28a8eb95496da4ea69ad458b9f8a8eb7d61 bash scripts/fetch_contextbench.sh
```

Place dataset files under `<contextbench>/data/` (or set `CONTEXTBENCH_PARQUET`).

Then:

```sh
pip install -e '.[bench,milvus,llm]'
python -m benchmarks.contextbench.runner --num-instances 32
```
