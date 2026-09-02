# Changelog

All notable changes to plumbline. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[SemVer](https://semver.org/).

## 0.3.0 — 2026-09-02

The delivery release: the instrument's findings were sound; the software
around them now holds to the same standard. 131 tests, and the published
numbers are pinned to the committed evidence in CI.

### Fixed
- **A plain `pip install` produces a working CLI.** The worked-example domains
  lived outside the package behind a repo-relative `sys.path` hack, so every
  scoring command failed with `ModuleNotFoundError` on a non-editable install.
  They are now real subpackages (`plumbline.domains.ap`,
  `plumbline.domains.accounts_payable`).
- **One scoring rule on every surface.** The CLI scored incomplete runs as
  violations while the store and the report excluded them, so the same run's
  same arm certified **41.1% grade F** from `certify` and **94.3% grade B**
  from the console. Every surface now excludes incomplete runs as missing
  data — disclosed, never silent — with `--include-errors` to opt back in.
  The committed certificates are regenerated under the unified rule.
- **Domains resolve loudly, never by accident.** Scoring commands take
  `--domain`, print which domain's policy they used, and refuse unknown names
  with the list of known ones — previously any run was silently scored
  against the 8-invoice AP policy.
- **Certificate provenance is anchored to the evidence.** The stamped commit
  came from the operator's working directory, so certifying from inside any
  other repository stamped that repository's commit into the workpaper. It
  now comes from the run directory's own repository, or reads
  `(no repository)`.
- **The study runner was import-broken at HEAD** (a dataclass field-order
  error) and nothing noticed, because no test touched it. Fixed, and the
  runner now has import/validation coverage.
- **Variant generation is reproducible across processes**: the seed no longer
  mixes in Python's per-process `hash()` salt.
- The live landing page claimed a "sanctions screen" was skipped — a phrase
  the repo's own publication guard bars as unsupported by the evidence (the
  skipped control is the vendor-status check). The page now says what the
  evidence shows, and the guard scans the page and the README, not only the
  LinkedIn kit.
- The README's "identical 768 trials, re-run" now discloses the replay
  methodology and the 52 free-form replays excluded as missing data; the
  unevidenced "scores 20/20" claim is gone; a test-suite comment calling
  credit exhaustion an "API outage" tells the truth now.
- `/api/health` reported 0.3.0 while the package said 0.2.0; the version has
  one source.
- The `anthropic` floor is `>=1.0` (the adapter uses a 1.x-only parameter);
  committed error traces no longer carry the author's local paths, and the
  runner scrubs future ones at write time.

### Added
- `tests/test_published_numbers.py`: every figure the README and the site
  quote — the 81.2/100.0/98.2 tool-fault row, the certified bounds, the 52
  excluded replays, the 2,082-trajectory total — recomputed from the
  committed traces on every push.
- Ruff (clean across src, tests, experiments, docs), Python 3.13 in CI, the
  publication guard as a CI step, CHANGELOG, CONTRIBUTING, SECURITY policy.

## 0.2.0 — 2026-08-30

The three studies (determinism, parity, retry), the retraction, the committed
evidence, the compliance/attestation layer, the store, the console, OTel
ingest, and the published report.
