# Contributing

Thanks for looking. Plumbline is deliberately small: a measurement core, a
statistics layer, and worked-example domains, all on the standard library.

## Setup

```bash
pip install -e ".[dev,server]"
pytest && ruff check src tests experiments docs
```

## The bar

Every change keeps four things true:

1. **The base install pulls nothing.** `dependencies = []`. The Anthropic SDK
   and FastAPI stay optional extras.
2. **Published numbers are pinned.** Anything the README or the site quotes is
   recomputed from the committed traces by
   `tests/test_published_numbers.py`. If your change legitimately moves a
   number, move the publication in the same commit — CI blocks the drift
   either way.
3. **Missing data is disclosed, never scored.** A run that did not complete is
   excluded and said so, on every surface, identically.
4. **Provenance is real.** A certificate stamps the evidence's repository and
   the domain whose policy scored it. Nothing is ever scored against a
   guessed policy.

## Good first issues

- A second model provider behind `adapters/`.
- Run the 20-invoice `accounts_payable` domain against a live model and
  publish the study.
- `plumbline diff <run> <trial-a> <trial-b>` in the terminal, mirroring the
  console's trace diff.
