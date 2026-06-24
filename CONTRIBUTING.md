# Contributing to Quant Copilot

Thanks for taking the time to make Quant Copilot better. This document
covers everything you need to send a PR.

## Development setup

```bash
git clone https://github.com/hasbunallah01/quant-copilot.git
cd quant-copilot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest httpx
pytest -v
```

`pytest -v` should report **39 / 39 passing** on Python 3.11.

## Coding conventions

* Python 3.11+, full type hints on public functions.
* `from __future__ import annotations` at the top of every new module.
* Google-style docstrings on public classes/functions. Keep them concise.
* No hard dependencies on the official Bitget SDK — keep the adapter thin
  and `requests`-based so it's auditable.
* New risk rules belong in `copilot/risk_engine.py` *and* `rules/default.yaml`
  *and* `docs/risk-engine.md`. Three places, one source of truth.

## Pull request flow

1. Fork & branch from `main` (`feat/short-name` or `fix/short-name`).
2. Run `pytest -v` and `python scripts/generate_verifiable_artifacts.py`
   before opening the PR.
3. Fill in the PR template (`.github/PULL_REQUEST_TEMPLATE.md`).
4. CI will run the test suite on Python 3.11.

## Adding a new anomaly type

1. Add a `feed()` branch in `copilot/detector.py` that builds an
   `Anomaly(type="...", severity=..., ...)`.
2. Add a corresponding template to `DiagnoseDoctor.TEMPLATES` in
   `copilot/ai_doctor.py`.
3. Add at least one pytest case in `tests/test_basic.py`.
4. Mention the new anomaly type in `README.md` and `docs/architecture.md`.

## Adding a new risk rule

1. Add the field to `rules/default.yaml` with a sensible default.
2. Implement the check in `copilot/risk_engine.py` in the documented
   rule order; update the order table in `docs/risk-engine.md`.
3. Add a pytest case asserting both the allow and block paths.
4. Add the verdict to `scripts/generate_verifiable_artifacts.py` so the
   verifiable artifact list grows with the project.

## Reporting bugs

Open an issue using the bug-report template (`.github/ISSUE_TEMPLATE/bug_report.md`).
Include:

* Quant Copilot version (`python -c "import copilot; print(copilot.__version__)"`)
* Python version (`python --version`)
* The relevant snippet of `logs/demo.log` (or your own log file) that
  triggered the bug
* Reproduction steps

## Security

For security issues, please **do not** open a public issue. See `SECURITY.md`.