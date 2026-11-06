# Linda Mini — browser-based fax routing sandbox

This is a **fictional, local portal** provided for the TriFetch engineering take-home. It simulates the workflow of reviewing inbound healthcare faxes, downloading scanned PDF packets, and assigning them to the correct internal staff member, document type, and folder. **All patient, organization and document information is synthetic.** It is not a medical system and should not be used with real patient data.

## Run the portal

Prerequisites: Python 3.11+ and a browser. The portal itself requires only FastAPI; **your automation/OCR code is your responsibility**.

```bash
python -m venv .venv
source .venv/bin/activate             # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

| Login field | Value |
|---|---|
| Username | `agent.demo` |
| Password | `demo-password-123` |

Inside the UI, choose **Fax Management → Inbound Faxes → fax row**. You can preview, **Download Fax PDF**, and later **Route / Assign Fax**. After entering **Assigned To**, **Document Type**, and **Document Folder**, choose **Review Routing → OK - Submit Routing**.

The inbox displays **six transmissions per page**. It does **not** display document classifications or extracted content. Downloaded PDFs have **no searchable text layer**: the content is embedded in scanned images, so actual OCR/vision is required.

## Your end-to-end workflow

Build a separate automation program that completes the flow for the queue:

1. Launch an **authenticated browser session A**. Use the portal navigation to open Inbound Faxes, inspect a fax, and download its PDF through the **Download Fax PDF** button. Record the stable fax ID shown on the page.
2. OCR or visually interpret **all pages of the downloaded PDF**. Extract an evidence-backed document classification, reference/identifier where available, patient name/date of birth where visible, and operational flags (e.g., urgent, unclear, mixed packet, possible resend). You may use a local OCR engine or a vision model. Do not infer missing values.
3. Make an explicit routing decision using the rules below. If uncertain, prefer the **Needs Review** route. If the document is a confirmed duplicate or is already routed, **do not submit a new route**.
4. Launch a **new, separate authenticated browser session B** **after the download**. Log in again. Use the UI to navigate and locate **the same fax ID** (not simply the same sender or date). Choose **Route / Assign Fax**, populate all three dropdowns, click **Review Routing**, then **OK - Submit Routing**.
5. Verify the saved result using the browser UI (fax status or Routed Documents). Persist your result so the worker can resume without duplicate actions if interrupted. Repeat for the remaining faxes.

**Important:** Session B must be a distinct login/browser context created after session A has downloaded the fax. Opening another tab in the same browser context does not count. The sandbox enforces this in the route submission path.

## Routing SOP

The UI deliberately does not make a routing decision for you. Determine the document type from the scanned packet and apply the following table. Select the exact values shown.

| Document content | Assigned To | Document Type | Document Folder |
|---|---|---|---|
| New patient referral / specialist consult request | Olivia Chen - Referrals | Referral | Referrals / New |
| Routine laboratory result | Noah Patel - Clinical Review | Lab Result | Clinical / Labs |
| Imaging / radiology report | Noah Patel - Clinical Review | Imaging Report | Clinical / Imaging |
| Request for prior authorization / coverage documentation | Ethan Brooks - Authorizations | Prior Authorization | Authorizations / Pending |
| Pharmacy prescription refill request | Ava Wilson - Nursing / Medication | Refill Request | Nursing / Medication |
| Request for release / copy of medical records | Mia Gomez - Medical Records | Records Request | Records / Requests |
| Explanation of Benefits / insurer remittance (EOB) | Liam Rivera - Billing | EOB | Billing / EOBs |
| Urgent/critical flag, mixed categories in one packet, unreadable/missing pages, or unclear classification | Jordan Ellis - Manual Review | Needs Review | Review / Unclassified |

**Safety & duplicate rules:**

- **Do not make any clinical interpretation, diagnosis, escalation call, or prescribing decision.** A document marked *critical* or *urgent* must be routed to **Manual Review**; include the flag in the routing note. This is a simulation of administrative routing, not patient care.
- A packet containing **more than one document category** must go to **Manual Review** instead of silently filing it as one type.
- A document without enough reliable OCR evidence for classification must go to **Manual Review**. Explain the uncertainty.
- Before routing, check whether the fax is already **Routed** in the UI. Do not modify a routed fax.
- Different fax IDs may contain a resend of the **same source document**. Use source document ID plus contextual evidence (not sender/time alone) to recognize confirmed resends. Route the **first instance only**; report the other fax as a skipped duplicate. If identity is uncertain, use review instead of an unsafe guess.

## Runtime characteristics

- There are **12 inbound fax transmissions**. Some are multi-page PDFs; some are similar by sender/time; some are rerouted already, duplicates, or exceptions.
- All browser actions must go through the portal's UI. Do not connect directly to the SQLite DB, read seeded PDFs from `data/faxes`, or call `__admin` endpoints as your automation's input or output mechanism. The folder exists only to make the local portal run.
- The available staff members, document types and folders are real UI dropdowns; your worker must select their correct values and **press OK**.
- The portal enforces two authenticated sessions separated by the fax download. It rejects an attempt to route in the same login session.
- Simulated failures are configurable; see below. Your worker should handle outcomes that are uncertain, not just explicit failures.
- No real services, credentials, patient data, EMR, email or fax vendor API are needed.

## Inject failures (optional while developing)

Stop the server, reset if desired, and restart with one of these environment variables:

```bash
LINDA_FAULT_MODE=download_503_once uvicorn app:app --host 127.0.0.1 --port 8000
LINDA_FAULT_MODE=session_expire_on_route_once uvicorn app:app --host 127.0.0.1 --port 8000
LINDA_FAULT_MODE=route_commit_then_503_once uvicorn app:app --host 127.0.0.1 --port 8000
```

- `download_503_once` — one download fails transiently before a PDF is delivered.
- `session_expire_on_route_once` — a routing session expires during the first submit.
- `route_commit_then_503_once` — the first routing action **succeeds** but returns a `503` instead of a success page. The worker must **recheck the UI** before attempting any retry.

For a fresh test state, stop the server and run:

```bash
python reset_sandbox.py
```

This deletes only the local sandbox database; the synthetic scanned PDFs remain unchanged.

## What to submit

Include: your working automation code, setup steps and exact run command, dependency list, a sample result artifact (`results.json` or equivalent), a concise architecture/failure-mode note, and either tests or a repeatable demo/script showing normal and exception handling. Provide a short screen recording or screenshots only if they materially help a reviewer reproduce the browser workflow.

A useful result for **each fax** should include its fax ID, OCR-derived classification and evidence/uncertainty, final action (`routed`, `needs_review`, `skipped_duplicate`, `already_routed`, or `failed`), selected values when routed, verification status, and error/recovery information. Do not fabricate a successful outcome from an HTTP response alone.

You may use external OCR/vision/AI tools as long as you disclose them and the reviewer can reproduce the relevant behavior. Prioritize correctness, safe routing, and demonstrable end-to-end execution over presentation polish.
