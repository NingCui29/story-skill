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

# Issue records and regression prevention

- Record discovered defects in the relevant repository issue log before closing
  the work: trigger, observed failure, root cause, fix, regression coverage, and
  the actual verification boundary. Preserve failed attempts and unresolved steps.
- Track workbench platform-draft defects in `docs/工作台草稿问题追踪.md`.
  Add focused regression checks for reproducible functional failures.
- Validate requested workbench flows through the workbench. Keep source tests,
  installed files, running services, and real platform results distinct; a passing
  simulation or file comparison must not close an unverified UI or platform step.
