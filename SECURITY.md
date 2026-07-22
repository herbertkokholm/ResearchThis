# Security Policy

## Supported versions

This project doesn't do versioned releases — only the latest commit on
`main` is supported.

## Reporting a vulnerability

Please don't open a public issue for a security vulnerability. Instead:

1. Preferred: use GitHub's private reporting — go to the
   [Security tab](https://github.com/herbertkokholm/ResearchThis/security) →
   "Report a vulnerability".
2. Or email thomas@herbertkokholm.dk with details.

This is a small, solo-maintained project, so responses are best-effort —
there's no guaranteed SLA, but reports will be looked at and, if valid,
fixed and disclosed once a fix is out.

## Scope

Relevant things to know when assessing impact:

- The portal has no user accounts or auth — it's a read-only public gallery.
- AWS S3 and Zotero credentials are supplied via environment variables
  (`.env` locally, platform secrets in deployment) and are never committed;
  see [`CONTRIBUTING.md`](CONTRIBUTING.md#data-and-secrets).
- The Zotero integration is read-only by design — this tool never writes to
  a connected Zotero library (see
  [§10 in `docs/SPEC.md`](docs/SPEC.md#10-zotero-integration)). A bug that
  broke that guarantee would be treated as a security issue, not just a bug.
