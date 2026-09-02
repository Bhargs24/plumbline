# Security policy

## Reporting

Please report vulnerabilities privately via
[GitHub security advisories](https://github.com/Bhargs24/plumbline/security/advisories/new)
rather than a public issue. You will get an acknowledgement within a week.

## Scope notes

- The CLI reads and writes local files only; nothing in the base install
  touches the network.
- The console and API bind to `127.0.0.1` by default and are read-only apart
  from `POST /ingest/traces`; starting anything that spends money against an
  API key is CLI-only by design.
- The study runner sends task prompts to the configured model provider and
  journals spend against a hard local cap; keys live in `.env`, which is
  gitignored and never read by the analysis or serving paths.
- Committed run evidence is scrubbed of machine-local paths at write time.
