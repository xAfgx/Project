# ARES SeleniumBase CDP PoC

This PoC keeps SeleniumBase completely external and unmodified. ARES does not vendor, fork, patch, or copy SeleniumBase internals.

## Architecture

```text
ARES / Node / Electron
        |
        | JSON over stdin/stdout (future adapter boundary)
        v
python/seleniumbase_cdp/worker.py
        |
        v
seleniumbase.sb_cdp.Chrome(...)
        |
        v
Dedicated SeleniumBase Chrome process + dedicated user_data_dir
```

Patchright is not imported or used by this worker. The PoC profile directory must not be opened concurrently by Patchright or another SeleniumBase process.

## Dependency

Install the pinned SeleniumBase package:

```bash
python -m pip install -r requirements-seleniumbase-cdp.txt
```

The version is pinned so ARES compatibility changes are explicit and reviewable. SeleniumBase itself stays untouched.

## Persistence probe

The probe starts a local HTTP server and launches the worker twice as two separate Python processes. Both processes receive the exact same SeleniumBase `user_data_dir`.

Process 1 writes a persistent cookie and LocalStorage value, quits SeleniumBase cleanly, and exits. Process 2 starts fresh, opens the same origin with the same profile directory, and verifies that both values survived the restart.

Temporary profile:

```bash
python python/seleniumbase_cdp/persistence_probe.py
```

Persistent dedicated profile on Windows:

```powershell
py python/seleniumbase_cdp/persistence_probe.py --profile-dir .ares-poc\seleniumbase\adam --headed
```

This PoC does not connect to ARES task execution, payment, address resolution, Captcha/Challenge/Solver code, or Patchright. Those integrations remain out of scope until the isolated SeleniumBase persistence path is proven stable.
