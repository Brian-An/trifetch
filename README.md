# Linda Mini fax routing worker

This is my worker for the Linda Mini take-home.
It downloads faxes through the portal, reads every page with local OCR, and uses the routing SOP to pick the assignment.
It then logs in through a fresh browser context, finds the same fax ID, submits the route, and checks what actually got saved.

I focused on making it safe to rerun, especially when a submit succeeds but the response fails.
The supplied portal and synthetic fax files are unchanged.

## Run it with Docker

You need Docker with Compose.
From the project folder, run:

```sh
docker compose up --build --abort-on-container-exit --exit-code-from worker
```

The first build installs the Python dependencies, Chromium, and Tesseract with English language data.
Once the worker finishes, Compose stops the portal too.
Results go to `artifacts/compose/results.json`.

If port 8000 is already taken, use a different host port:

```sh
LINDA_PORT=8022 docker compose up --build --abort-on-container-exit --exit-code-from worker
```

The portal keeps its database in a Docker volume.
The worker keeps its checkpoints and downloads in `artifacts/compose`.
Running the command again checks the current UI state and resumes the same history.

## Run it locally

The package requires Python 3.11 or newer.
I tested with Python 3.12 on macOS and in a Linux container.

First, install Tesseract and make sure `tesseract` is on your PATH.

On macOS:

```sh
brew install tesseract
```

On Debian or Ubuntu:

```sh
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
```

Then install the Python dependencies and browser:

```sh
python3.12 -m venv .worker-venv
source .worker-venv/bin/activate
python -m pip install --require-hashes -r requirements-worker.txt
python -m pip install --no-deps -e .
python -m playwright install chromium
```

On Linux, use `python -m playwright install --with-deps chromium` for the last step so the browser's system dependencies get installed too.

Start the portal in one terminal with the environment activated:

```sh
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

In a second terminal, activate the same environment and start the worker:

```sh
source .worker-venv/bin/activate
python -m linda_worker --url http://127.0.0.1:8000 --state-dir artifacts/run
```

The default login is the supplied training account: `agent.demo` / `demo-password-123`.
For a native run, you can override it with `LINDA_USERNAME` and `LINDA_PASSWORD`.
`LINDA_URL` sets the default portal URL if `--url` is omitted.

On a native installation, add `--headed` to watch the browser.
Add `--screenshots` to save confirmation and verification screenshots alongside the results.

## Dependencies

All document processing runs locally.
There are no model API calls, hosted OCR services, or API keys to configure.

| Dependency | What I use it for |
|---|---|
| Playwright and Chromium | Logging in, navigating, downloading, filling forms, and checking saved routes |
| PDFium through `pypdfium2` | Validating PDFs and rendering their pages |
| Tesseract, `pytesseract`, and Pillow | OCR and image preprocessing |
| Pydantic | Validating structured records and checkpoints |
| SQLite and `filelock` | Saving progress and keeping one worker in a state directory |
| `psutil` | Cleaning up the processing process and its children |
| FastAPI, Uvicorn, and `python-multipart` | Running the supplied portal |

The pinned dependencies are in `pyproject.toml`.
`requirements-worker.txt` includes the hash-locked Python dependencies for the worker, portal, and checks.
`uv.lock` is also included.

## Results and resume

`examples/results.json` is a sample from a completed Docker run.
On a fresh sandbox, the result is seven ordinary routes, three manual-review routes, one skipped duplicate, and one already-routed fax.

Each fax result includes its ID, page-by-page OCR text, classification evidence, visible source and patient identifiers, flags, uncertainties, selected and observed routing values, and recovery history.
Missing identifiers stay missing.

A skipped duplicate stays New in the portal because there is no control for dismissing it as a duplicate.
The JSON records `skipped_duplicate` and the fax ID of the matching completed assignment.

To resume after a failure or interruption, run the same command with the same state directory.
Keep the checkpoints so the worker can reconcile an interrupted submit with the UI.
Faxes still shown as New are downloaded again; byte-identical PDFs can reuse saved OCR.
Use a different state directory for a different portal URL or account.

## Timeouts and exit codes

The whole-run deadline defaults to 900 seconds.
It covers processing in the worker, including downloads and native PDF/OCR work.
A separate supervisor stops the processing process and its children when that budget runs out.

For a shorter native run:

```sh
python -m linda_worker --url http://127.0.0.1:8000 --state-dir artifacts/run --deadline-seconds 120
```

For Docker, set `LINDA_DEADLINE_SECONDS=120` before the Compose command.
After a deadline expires, `results.json` has `run_status: "timed_out"` and the last saved checkpoints.
The next run checks the UI again before making another submit.

| Exit code | Meaning |
|---|---|
| `0` | All discovered faxes are accounted for and verified |
| `1` | The run failed or has unresolved work |
| `2` | Invalid CLI arguments, or another worker owns the state directory |
| `124` | The processing deadline expired |
| `130` | Interrupted by Ctrl-C or SIGTERM |

The defaults are three attempts per operation and a 10-second browser action timeout.
Use `--attempts` and `--timeout-ms` to adjust those.

## Run the checks

With the local environment activated:

```sh
ruff check linda_worker tests
ruff format --check linda_worker tests
mypy linda_worker
pytest --run-e2e -q
```

The latest full Linux run passed 73 tests.
The suite covers the full queue, OCR at four rotations, exact routing values, separate sessions, all three supplied fault modes, restart recovery, and an already-routed resend with a still-New original.
It also covers stalled downloads, native parser stalls, deadline expiry after a submit, process cleanup, and SIGTERM.

For a shorter run, `pytest -q` skips the browser integration tests but still runs real OCR tests.
The browser tests start their own temporary portals and check results through the UI.

After building the Docker image, you can run the full suite there too:

```sh
docker compose run --rm --no-deps worker python -m pytest --run-e2e -q
```

## Try the supplied faults

To try a fault in Docker from a fresh portal state:

```sh
docker compose down --volumes
LINDA_FAULT_MODE=route_commit_then_503_once docker compose up --abort-on-container-exit --exit-code-from worker
```

The other modes are `download_503_once` and `session_expire_on_route_once`.
Each fault fires once per fresh portal database.
This reset removes the portal volume; the worker's history stays in `artifacts/compose`.
New faxes are downloaded again before routing, so keeping that history across a portal reset is supported.

For a native portal, stop it, run `python reset_sandbox.py`, then restart it with the chosen `LINDA_FAULT_MODE` environment variable.

The design decisions, failure handling, and limits are in [the engineering notes](ARCHITECTURE.md).
