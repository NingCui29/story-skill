# Project constraints

- Do not implement, import, depend on, bundle, or reintroduce Qt, PySide, PyQt,
  Shiboken, Qt WebEngine, PHP, or wrappers that require them.
- The Windows desktop host uses C# / WPF / WebView2. Keep the Python manuscript
  service and the Mac Swift client compatible with existing author data.
- Run `python scripts/check_forbidden_technologies.py --source` for dependency
  and desktop changes. Windows builds must also scan the entire published and
  installed application directories using `--root <directory>`.
- Historical release receipts and external source links may name old or external
  technologies. They must not become current build inputs or dependencies.
- See `docs/技术约束.md` for the policy and desktop validation boundaries.

# Versioning

- Follow `docs/版本管理.md` for version decisions and release preparation.
- Keep host skill packages aligned with the core version; version Mac and Windows
  clients independently, including changes to their bundled manuscript service.
- Preserve published tags, artifacts, and historical receipts. Record development,
  build, release, installation, and running-service states separately.

# Project management and traceability

- Treat the user's project-management, incident-review, and GitHub version-control
  requirements as hard project constraints. Explain the exact intended changes
  before modifying code, configuration, or the installed application.
- Record each change and incident: symptoms, scope, observed evidence, confirmed
  root cause or explicit uncertainty, prior occurrences, previous working version,
  affected versions, remedy, validation, residual risk, and rollback. Preserve
  success records as well as failure records; distinguish similar errors from
  proven identical root causes. Do not store manuscripts, credentials, tokens,
  or private browser/session data in project or diagnostic records.
- Give errors stable queryable codes and an incident identifier. Unknown failures
  must remain distinguishable as unknown; do not invent a root cause or treat a
  structural fingerprint as proof that two errors have the same cause.
- For every fix, run relevant regression checks for previously observed failures,
  retain failed or flaky results, and verify the complete authorized workflow.
  Distinguish source tests from build, installation, running-service and real
  platform verification. Never declare end-to-end success from an intermediate
  checkpoint, and do not bypass host authorization or replay uncertain writes.
- Sync changes promptly to GitHub only after the relevant working behavior has
  been verified, through a reviewable branch and commit; do not represent an
  unverified or failing candidate as a usable version. Project rules and incident
  records may be synchronized separately when they accurately disclose status;
  preserve unrelated existing changes, historical tags and artifacts. If a change
  cannot yet be safely isolated, tested or synchronized, record its exact state
  and blocker rather than claiming it was pushed or silently mixing work.
- Apply these rules separately to Windows and Mac. Prefer the web workbench unless
  the user asks to open or validate a particular client. See
  `docs/Windows草稿项目管理.md` and `docs/Windows草稿连接故障复盘.md`.
