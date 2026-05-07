# Migration Checker

`migration_checker` is a standalone static analysis tool for checking Python library migrations. It is built on `LibCST` and uses a rule-driven architecture to detect high-confidence issues such as leftover source-library usage, target API misuse, missing `await` or context-manager usage, downstream return-use mistakes, and incomplete migrations.

## Repository Structure

```text
migration_checker/
├── evaluation/              # Evaluation script, cached benchmark data, and generated results
├── fixtures/                # Small Python files for analysis/regression
│   ├── argparse/
│   ├── flask/
│   ├── matplotlib/
│   ├── pandas/
│   └── requests/
├── rules/                   # YAML rule files grouped by migration domain
│   ├── data_processing/
│   ├── http_clients/
│   ├── plotting/
│   ├── utility/
│   └── web_framework/
├── src/                     # migration_checker source code
│   └── migration_checker/
│       ├── __main__.py
│       ├── collector.py
│       ├── detector.py
│       ├── main.py
│       └── rules.py
├── tests/                   # Regression tests for rules, analysis, and evaluation
├── pyproject.toml
├── requirements.txt
└── README.md
```

## Setup Instructions

### Prerequisites
- Python 3.9+
- Git

### 1. Clone the repository

```bash
git clone https://github.com/MatthiasKebede/migration_checker.git
cd migration_checker
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate   # `.venv/Scripts/activate` for Windows
```

### 3. Install the package

```powershell
python -m pip install -e .
```

## CLI Usage

### Arguments

- `file_or_directory_path`: Path to the Python file or directory to analyze.
- `--source`: Source library name used to select a rule file.
- `--target`: Target library name used to select a rule file.
- `--rule-file`: Optional YAML rule file override.
- `--output-json`: Output diagnostics as JSON.

### Examples

```bash
python -m migration_checker fixtures/pandas --source pandas --target polars --output-json
python -m migration_checker fixtures/requests/sample_httpx.py --source requests --target httpx
python -m migration_checker fixtures/requests/sample_httpx.py --rule-file rules/http_clients/requests_to_httpx.yml
```

## How It Works

The checker follows a rule-guided static analysis pipeline:

1. `rules.py` resolves a source/target pair to a YAML rule file, validates the schema, and normalizes it into lookup structures for imports, calls, accesses, usage constraints, return semantics, and severities.
2. `collector.py` parses Python with `LibCST` and collects imports, aliases, qualified calls, explicit attribute accesses, bindings, decorator usage, `await` usage, context-manager usage, and simple local provenance facts.
3. `detector.py` runs the current check families:
   - leftover source imports, calls, and explicit source accesses
   - target API keyword and positional-argument contract checks
   - missing `await` and required context-manager usage
   - downstream return-use checks such as renamed or forbidden attribute access
   - mixed source/target assignments and nearby duplicate migration usage
4. `main.py` runs the selected checks over one file or directory and emits text or JSON diagnostics.
5. `evaluation/scripts/evaluate.py` reuses the same analysis pipeline across cached benchmark snapshots.

The analysis is intentionally conservative and aims to catch high-confidence migration issues rather than infer full program semantics. It is local and scope-based, with no interprocedural reasoning or type inference.


## Rule Format

```yaml
pair:
  source: requests
  target: aiohttp

libraries:
  source_roots:
    - requests
  target_roots:
    - aiohttp

diagnostics:
  severity:
    leftover_source_import: error
    leftover_source_call: error
    renamed_keyword_argument: error
    forbidden_keyword_argument: error
    missing_required_keyword: error
    positional_argument_misuse: error
    missing_await: error
    missing_context_manager: error
    mixed_source_target_assignment: warning
    renamed_attribute_access: error
    forbidden_attribute_access: error
    duplicate_migration_usage: warning

rules:
  - id: source-import-root
    kind: import
    source:
      module: requests
    target:
      module: aiohttp

  - id: requests-get
    kind: call
    source:
      symbol: requests.get
    target:
      symbol: aiohttp.ClientSession.get
    contract:
      renamed_keywords: {}
      forbidden_keywords: []
      required_keywords: []
      require_keywords: false
    usage:
      await_required: false
      context_manager: async_with
    return:
      tag: http_response
      renamed_attributes:
        status_code: status
      forbidden_attributes:
        - text
        - json
        - content
      await_required_methods:
        - text
        - json

  - id: request-form-access
    kind: access
    source:
      symbol: flask.request.form
    target:
      symbol: quart.request.get_json
    usage:
      await_required: true
      access_kind: call
```

Notes:

- `libraries.source_roots` drives leftover import detection.
- `call` rules drive source-call leftovers, target API contract checks, usage checks, and downstream return-use checks.
- `access` rules drive explicit source-access leftovers and explicit target access/call checks.
- Explicit source accesses are currently reported under the historical diagnostic code `leftover_source_call` for compatibility with the existing rules, tests, and evaluation artifacts.


## Coverage

Shipped rule coverage currently includes:

- `requests -> aiohttp`, `httpx`, `urllib3`
- `pandas -> dask.dataframe`, `polars`, `tablib`
- `matplotlib.pyplot -> bokeh.plotting`, `plotly.express`, `seaborn`
- `argparse -> click`, `configargparse`, `docopt`
- `flask -> bottle`, `fastapi`, `quart`

This coverage is broader than the main benchmark evaluation set. The primary benchmark-backed evaluated pairs are:

- `requests -> aiohttp`
- `argparse -> click`
- `argparse -> docopt`
- `argparse -> configargparse`
- `flask -> quart`
- `flask -> fastapi`
- `flask -> bottle`

Pairs outside that set are currently supported by rules and fixtures, but not backed by the same direct benchmark evaluation workflow.


## Running Tests

The full regression suite includes evaluator tests that expect a sibling checkout of `PyMigBench` in order to read migration metadata from `PyMigBench/data/migration/`.

From the `migration_checker/` directory:

```bash
python -m unittest discover -s tests -v
```

## Reproducing the Evaluation

The evaluator is built around `PyMigBench` version `2.2.5`. It discovers feasible benchmark-backed pairs by intersecting:

- the local shipped rule files
- the migration pairs that appear directly in `PyMigBench`

It then evaluates three tracks:

- `clean-post`: false-positive pressure on post-migration files
- `fault-inject`: controlled recall against injected migration faults on post-migration files
- `pre-leftover`: leftover-source detection on pre-migration files

### PyMigBench setup

Clone `PyMigBench` as a sibling repository - the evaluator takes the `PyMigBench` root path as its first positional argument. See https://github.com/ualberta-smr/PyMigBench#installation.

### Cache and GitHub token

Snapshots are cached under `evaluation/data/cache/`. A GitHub token is only needed to populate missing snapshots or to force refreshes. **Note: the first evaluation run will likely take a significant of time due to not having access to cached data.**

macOS / Linux:

```bash
# macOS / Linux
export GITHUB_TOKEN=your_token_here

# Windows PowerShell
$env:GITHUB_TOKEN="your_token_here"
```

You can also place the token in `evaluation/scripts/.env`:

```dotenv
GITHUB_TOKEN=your_token_here
```

### Commands

Run all feasible benchmark-backed pairs and write JSON plus CSV summaries:

```bash
python evaluation/scripts/evaluate.py ../PyMigBench --mode all --output-json repro_data.json
```

Regenerate the committed frozen artifact filenames:

```bash
python evaluation/scripts/evaluate.py ../PyMigBench --mode all --output-json evaluation/data/results/original_data.json
```

Run only the clean post-migration baseline:

```bash
python evaluation/scripts/evaluate.py ../PyMigBench --mode clean-post
```

Run one pair:

```bash
python evaluation/scripts/evaluate.py ../PyMigBench --source flask --target quart
```

Run only pair-specific injectors:

```bash
python evaluation/scripts/evaluate.py ../PyMigBench --source requests --target aiohttp --mode fault-inject --pair-faults only
```

Refresh cached snapshots before falling back to the local cache:

```bash
python evaluation/scripts/evaluate.py ../PyMigBench --mode all --refresh-cache --output-json refreshed_eval.json
```

Important options:

- `--mode {clean-post,fault-inject,pre-leftover,all}`
- `--source` / `--target`
- `--rule-file`
- `--cache-dir <path>`
- `--output-json <path>`
- `--fault-types <csv>`
- `--pair-faults {default,none,only}`
- `--refresh-cache`
- `--limit <n>`

### Evaluation outputs

When `--output-json <path>` is provided, the evaluator writes:

- one JSON report at the requested path
- `<stem>_clean_post_summary.csv`
- `<stem>_fault_inject_summary.csv`
- `<stem>_pre_leftover_summary.csv`
- `<stem>_by_code.csv`

The committed frozen evaluation artifacts live under `evaluation/data/results/original_data*`.


## JSON Output

Verifier diagnostics are keyed by file path and each diagnostic includes:

- `line`
- `message`
- `code`
- `severity`

Evaluation JSON reports include top-level summaries such as:

- `benchmark_version`
- `evaluated_pairs`
- `summary`
- `summary_by_code`
- `pair_reports`
- `cache_stats`


## Limitations

- The analysis is local and scope-based.
- There is no interprocedural reasoning or type inference.
- Rule quality depends on hand-authored migration rules.
- Duplicate-migration detection is intentionally shallow and proximity-based.
- `fault-inject` measures detector fidelity against controlled injected faults, not recall over a natural bug corpus.


## GenAI Disclosure

- **OpenAI Codex** - used to support the development and evaluation of `migration_checker`
