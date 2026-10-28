# Full-suite runner import-path diagnosis

The two first-ready failures were caused by the validation runner's Python import path. They were not a reproduced regression in the four production fixes, a proven scheduler problem, or a failure of the 75-second resume contract.

The original runner executed as a file under `benchmarks/results/development-20261023-four-followups/`. Python therefore put that directory at `sys.path[0]`. Its `os.chdir(ROOT)` changed the working directory without adding the repository root to the import path. `unittest.discover(ROOT / "tests")` added the test directory, which supports imports such as `import test_publish`, but did not make the sibling package import `from tests.test_publish import story` available.

## Observed evidence

| Probe | Result |
| --- | --- |
| Original full runner, twice | Both stopped at test 145, the first controller test, while waiting for its initial ready state. Preserved as `full-suite-initial-interrupted.log` and `full-suite-second-failed.log`; the former filename is historical, and its recorded result is a test failure, not an interruption diagnosis. |
| Full discovery with repository root available; then only the affected test | 2613 discovered; the test passed in 0.028 seconds. Importing every test module alone did not reproduce the failure. |
| The exact first 145 tests with repository root available | All 145 passed. The transient diagnostic runner used stdin execution and `PYTHONPATH=tests`, so the repository root remained available through the empty initial path entry. |
| Script-style path without repository root; full discovery followed by only the affected test | 2286 discovered, with loader errors. The first recorded loader error was `ModuleNotFoundError: No module named 'tests'` while importing `test_platform_browser_batch`. The controller test failed in 4.048 seconds. The count difference includes failed-import placeholders and must not be treated as a complete test inventory. |
| Same missing-root path; direct entry to `FakeCore._pinned_directory` | Raised `ModuleNotFoundError: No module named 'tests'` at `tests/test_chrome_worker_controller.py:559`, on `from tests.test_publish import story`. |
| Actual temporary script entry point outside the repository; explicitly insert `ROOT`; then full discovery and the affected test | Initial path was the temporary script directory, not the repository. Discovery returned 2613 with `loader.errors == []`; the controller test passed in 0.027 seconds. No `PYTHONPATH` was supplied. |

The instrumented failure had already reached `state=failed`, `error=platform_chrome_worker_failed`, and `failure_phase=created` after 27 milliseconds. It had no process or client. Diagnostics recorded `created`, `connection_failed`, `stop_requested`, and `closed`. The generic fixture wait then continued until its four-second limit. This distinguishes the actual startup import failure from a worker that was merely slow to become ready.

The affected test uses `FakeCore` and `FakeProcess`. Its fake binding and health functions do not invoke the production `BrowserAdapter`, the new editor-marker validation, or the JavaScript driver. The controller source and this test file were unchanged in this repair round. The first-ready assertion occurs before `expire_after_ready_checkpoint`, so none of the 75-second recovery assertions had run.

## Reproduction setup

All probes used the bundled Python 3.12 runtime, `-B`, `PYTHONDONTWRITEBYTECODE=1`, and the bundled Node directory on `PATH`. They ran only local synthetic fixtures. No real book, account, platform, browser, or model worker was used.

The original file-entry command was equivalent to:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B benchmarks/results/development-20261023-four-followups/run_validation.py
```

The isolated negative probe used the following path setup before discovery, recreating that file-entry import path without modifying repository source:

```python
ROOT = Path.cwd()
sys.path[0] = str(ROOT / "benchmarks/results/development-20261023-four-followups")
loader = unittest.TestLoader()
suite = loader.discover(str(ROOT / "tests"))
case_module = sys.modules["test_chrome_worker_controller"]
case = case_module.ControllerTests(
    "test_75_second_model_gap_is_not_ready_until_the_same_next_call_has_fresh_health"
)
unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([case]))
```

The positive file-entry probe used the same discovery and test selection after:

```python
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
```

Temporary instrumentation collected the entry's state and diagnostic events if the fixture wait failed. It changed neither the four-second fixture deadline nor the production connection deadline.

## Correction and validation boundary

The saved `run_validation.py` now adds the repository root explicitly, uses a fresh `TestLoader`, fails immediately if discovery reports import errors, records the discovered count, and requires the successful full run to execute that count. This aligns file-entry execution with the repository's module-style unittest import context and prevents import failures from silently reducing coverage.

No production source, fixture, timeout, or assertion was changed to resolve this runner error. The independent positive probe confirms the import-path correction. The final complete regression result is recorded separately by the corrected runner; the diagnostic probes do not substitute for it.
