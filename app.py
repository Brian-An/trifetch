"""Fictional Linda-style fax portal for a synthetic browser-automation assessment.

This file implements ONLY the simulator. The candidate is expected to implement
an independent automation client. Fax content is stored as image-only PDF files;
there is no OCR endpoint or pre-computed classification API in the portal.

All people, organizations and documents are fictional. Not a clinical product.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "faxes"
DB = Path(os.getenv("LINDA_DB_PATH", str(ROOT / "portal.sqlite3")))
FAULT_MODE = os.getenv("LINDA_FAULT_MODE", "none")
ADMIN_TOKEN = os.getenv("LINDA_ADMIN_TOKEN", "local-evaluator-only")
USERNAME = "agent.demo"
PASSWORD = "demo-password-123"
SESSION_SECONDS = 3600
app = FastAPI(title="Linda Mini - Synthetic Fax Portal", docs_url=None, redoc_url=None, openapi_url=None)

ASSIGNEES = [
    ("olivia", "Olivia Chen - Referrals"),
    ("noah", "Noah Patel - Clinical Review"),
    ("ethan", "Ethan Brooks - Authorizations"),
    ("ava", "Ava Wilson - Nursing / Medication"),
    ("mia", "Mia Gomez - Medical Records"),
    ("liam", "Liam Rivera - Billing"),
    ("jordan", "Jordan Ellis - Manual Review"),
]
DOCUMENT_TYPES = ["Referral", "Lab Result", "Imaging Report", "Prior Authorization", "Refill Request", "Records Request", "EOB", "Needs Review"]
FOLDERS = ["Referrals / New", "Clinical / Labs", "Clinical / Imaging", "Authorizations / Pending", "Nursing / Medication", "Records / Requests", "Billing / EOBs", "Review / Unclassified"]

CSS = """
:root{font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#183346;background:#f5f7fa;font-synthesis:none}*{box-sizing:border-box}
body{margin:0}a{color:#1c6ea3;text-decoration:none}a:hover{text-decoration:underline}
.top{height:64px;background:#143046;color:white;display:flex;align-items:center;justify-content:space-between;padding:0 28px;position:sticky;top:0;z-index:10}
.brand{font-size:23px;font-weight:800;letter-spacing:-1px}.brand span{font-weight:400;color:#8fb5c7;font-size:13px;letter-spacing:.08em;margin-left:12px}
.userbar{display:flex;gap:14px;align-items:center;font-size:12px;color:#cfe0ea}.userbar button{font-size:12px;padding:7px 10px;background:#284d65;border:1px solid #55718a}
.shell{display:flex;min-height:calc(100vh - 64px)}.side{width:245px;flex-shrink:0;background:#eaf0f5;border-right:1px solid #d8e1e9;padding:26px 12px}
.sidehead{color:#7b91a2;text-transform:uppercase;font-size:10px;letter-spacing:.15em;font-weight:800;padding:2px 18px 14px}
.nav{display:block;padding:13px 16px;border-radius:7px;font-size:14px;color:#2e4c63;margin:4px 3px;font-weight:600}.nav.active{background:#d4e6f3;color:#0d5d8a}.nav.group{font-weight:800;color:#243f52}.nav.child{padding-left:32px;font-weight:500}.nav:hover{background:#e0edf6;text-decoration:none}
.main{max-width:1230px;padding:30px 36px 72px;flex:1;min-width:0}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:#648093;font-weight:800}
h1{font-size:28px;letter-spacing:-.65px;margin:9px 0 16px;color:#19384e}h2{font-size:18px;margin:4px 0 14px}p{line-height:1.55}
.subtitle{color:#6d8190;font-size:14px;margin-top:-6px}.card{background:#fff;border:1px solid #dce6ec;border-radius:10px;box-shadow:0 2px 14px #112a3f08;padding:22px;margin:19px 0}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.metric{font-size:35px;font-weight:800;margin:8px 0;color:#173d5a}.metriccap{font-size:13px;color:#6f8494}
table{border-collapse:collapse;width:100%;font-size:13px}th{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:#617c8f;background:#edf3f7;text-align:left}th,td{padding:12px 13px;border-bottom:1px solid #e3ecf1}tr:last-child td{border-bottom:0}tr:hover td{background:#fbfdff}
.badge{display:inline-block;padding:5px 10px;border-radius:30px;font-size:11px;font-weight:750;background:#edf4f8;color:#486f87}.badge.done{background:#e4f7f0;color:#227253}.badge.new{background:#fff2dc;color:#936016}
label{font-size:13px;font-weight:750;display:block;margin:14px 0 7px;color:#3e586c}input,select,textarea{font:inherit;width:100%;border:1px solid #c3d2dc;border-radius:7px;background:white;padding:10px 11px;font-size:14px}input:focus,select:focus{outline:2px solid #72b2d1}
button,.btn{border:0;border-radius:7px;background:#1673a7;color:white;font-size:13px;font-weight:750;padding:11px 17px;cursor:pointer;display:inline-block;text-decoration:none}button:hover,.btn:hover{background:#095a8b;text-decoration:none}.secondary{background:#e6f0f6;color:#1f5a78}.secondary:hover{background:#d7e8f3}.disabled{opacity:.6;pointer-events:none}
.controls{display:flex;align-items:end;gap:10px;flex-wrap:wrap}.controls label{margin:0}.controls input{width:260px}.two{display:grid;grid-template-columns:1.45fr 1fr;gap:22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}
.small{font-size:12px;color:#7890a0}.notice{border-radius:7px;background:#eff7fb;border:1px solid #c3dfea;padding:12px 14px;font-size:13px;color:#305b70}.warning{background:#fff6e9;border-color:#efd7a9;color:#805d1a}.error{background:#fff0ee;border-color:#efc7c4;color:#923e36}.spaced{display:flex;justify-content:space-between;align-items:center;gap:14px}.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;background:#eef3f6;padding:3px 6px;border-radius:4px}
.preview{width:100%;height:600px;border:1px solid #dbe6ec;border-radius:7px;background:#eee}.crumb{font-size:13px;color:#698499;margin:0 0 17px}.actions{display:flex;gap:10px;flex-wrap:wrap}.pagination{display:flex;justify-content:space-between;align-items:center;margin:18px 0}
.modal-backdrop{display:none;align-items:center;justify-content:center;position:fixed;inset:0;background:#0c2035b3;z-index:50}.modal{background:white;width:490px;max-width:94vw;padding:25px;border-radius:13px;box-shadow:0 18px 60px #02101e66}.modal-backdrop.show{display:flex}.kv{display:grid;grid-template-columns:145px 1fr;row-gap:11px;font-size:13px}.kv dt{color:#7d8d9b}.kv dd{margin:0;font-weight:650}
.login{max-width:430px;margin:11vh auto}.login .brand{color:#173c54;margin-bottom:25px}.login .card{padding:28px}.fine{font-size:11px;color:#8ca1af;margin-top:20px}
@media(max-width:850px){.side{width:180px}.main{padding:24px}.two{grid-template-columns:1fr}.metrics{grid-template-columns:1fr}.grid{grid-template-columns:1fr}}
"""


def esc(value):
    return escape(str(value if value is not None else ""), quote=True)


def now():
    return time.time()


def stamp(ts=None):
    return datetime.fromtimestamp(ts or now(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@contextmanager
def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS faxes (
          fax_id TEXT PRIMARY KEY, sender TEXT NOT NULL, received_at TEXT NOT NULL,
          pages INTEGER NOT NULL, filename TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY, username TEXT NOT NULL, created_at REAL NOT NULL,
          expires_at REAL NOT NULL, csrf TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS downloads (
          id INTEGER PRIMARY KEY AUTOINCREMENT, fax_id TEXT NOT NULL, session_id TEXT NOT NULL,
          downloaded_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS routes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, fax_id TEXT NOT NULL UNIQUE, doc_type TEXT NOT NULL,
          assignee TEXT NOT NULL, folder TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
          session_id TEXT NOT NULL, routed_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, fax_id TEXT,
          session_id TEXT, detail TEXT NOT NULL DEFAULT '', at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fault_events (name TEXT PRIMARY KEY, fired_at REAL NOT NULL);
        """)
        if c.execute("SELECT COUNT(*) FROM faxes").fetchone()[0] == 0:
            manifest = json.loads((DATA_DIR / "manifest.json").read_text())
            c.executemany("INSERT INTO faxes VALUES(?,?,?,?,?)", [
                (x["fax_id"], x["sender"], x["received_at"], x["pages"], x["filename"]) for x in manifest
            ])
            c.execute("""INSERT INTO routes(fax_id,doc_type,assignee,folder,notes,session_id,routed_at)
                 VALUES(?,?,?,?,?,?,?)""", ("FX-211", "Imaging Report", "noah", "Clinical / Imaging",
                                             "Routed by clinic staff before this assessment", "STAFF", now()-7200))


def audit(c, event, fax_id=None, session_id=None, detail=""):
    c.execute("INSERT INTO audit_events(event,fax_id,session_id,detail,at) VALUES(?,?,?,?,?)",
              (event, fax_id, session_id, detail, now()))


def fault_once(c, name):
    if FAULT_MODE != name:
        return False
    previous = c.execute("SELECT 1 FROM fault_events WHERE name=?", (name,)).fetchone()
    if previous:
        return False
    c.execute("INSERT INTO fault_events VALUES(?,?)", (name, now()))
    c.commit()
    return True


def get_session(request, c):
    sid = request.cookies.get("linda_sid", "")
    if not sid:
        return None
    return c.execute("SELECT * FROM sessions WHERE id=? AND expires_at>?", (sid, now())).fetchone()


def require(request, c):
    session = get_session(request, c)
    if session:
        return session, None
    next_url = quote(request.url.path, safe="")
    return None, RedirectResponse("/login?next=" + next_url, status_code=303)


def html(title, inner, session=None, active="", status=200):
    if session is None:
        return HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{esc(title)} | Linda Mini</title><style>{CSS}</style></head>
        <body><div class='login'><div class='brand'>linda <span>MINI / FAX PORTAL</span></div>{inner}<div class='fine'>Fictional browser training portal. No real patient information.</div></div></body></html>""",status_code=status)
    user = esc(session['username'])
    ssid = esc(session['id'][:8])
    def nav(name, url, key, cls=""):
        return f"<a class='nav {cls} {'active' if active==key else ''}' href='{url}'>{name}</a>"
    sidebar = "".join([
        "<div class='sidehead'>Workspace</div>", nav("Overview", "/dashboard", "dashboard"),
        nav("Fax Management", "/faxes", "faxes", "group"),
        nav("Inbound Faxes", "/faxes/inbound", "inbound", "child"),
        nav("Routed Documents", "/work-queue", "queue", "child"),
        nav("Document Folders", "/folders", "folders"),
    ])
    return HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{esc(title)} | Linda Mini</title><style>{CSS}</style></head>
    <body><header class='top'><div class='brand'>linda <span>MINI / FAX MANAGEMENT</span></div><div class='userbar'><span>Signed in: {user}</span><span>Session {ssid}</span><form action='/logout' method='post'><button type='submit'>Sign out</button></form></div></header>
    <div class='shell'><aside class='side'>{sidebar}</aside><main class='main'>{inner}<div class='fine'>Fictional assessment environment. Synthetic documents only.</div></main></div></body></html>""", status_code=status)


def notice(text, kind=""):
    return f"<div role='alert' class='notice {kind}'>{esc(text)}</div>"


def options(items, placeholder="Select an option"):
    return "<option value='' selected disabled>" + esc(placeholder) + "</option>" + "".join(
        f"<option value='{esc(v)}'>{esc(label)}</option>" for v, label in items)


init_db()


@app.get("/")
def root(request: Request):
    with conn() as c:
        ss = get_session(request, c)
    return RedirectResponse("/dashboard" if ss else "/login", status_code=303)


@app.get("/login")
def login_form(next: str = "/dashboard", error: str = ""):
    target = next if next.startswith("/") and not next.startswith("//") else "/dashboard"
    body = f"""<section class='card'><div class='eyebrow'>Secure workspace</div><h1>Sign in to fax management</h1>
    <p class='subtitle'>Use the training account from the exercise README.</p>
    {notice('Username or password was incorrect.', 'error') if error else ''}
    <form method='post' action='/login'><input type='hidden' name='next' value='{esc(target)}'/>
      <label for='username'>Username</label><input id='username' autocomplete='username' name='username' required/>
      <label for='password'>Password</label><input id='password' type='password' autocomplete='current-password' name='password' required/>
      <p style='margin:21px 0 0'><button type='submit' style='width:100%'>Sign in</button></p>
    </form></section>"""
    return html("Sign in", body)


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/dashboard")):
    if not secrets.compare_digest(username, USERNAME) or not secrets.compare_digest(password, PASSWORD):
        return RedirectResponse("/login?error=1&next=" + quote(next, safe=""), status_code=303)
    with conn() as c:
        sid = secrets.token_urlsafe(24)
        c.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (sid, username, now(), now() + SESSION_SECONDS, secrets.token_urlsafe(24)))
        audit(c, "login", session_id=sid)
    target = next if next.startswith("/") and not next.startswith("//") else "/dashboard"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie("linda_sid", sid, httponly=True, samesite="lax", max_age=SESSION_SECONDS)
    return response


@app.post("/logout")
def logout(request: Request):
    with conn() as c:
        ss = get_session(request, c)
        if ss:
            c.execute("DELETE FROM sessions WHERE id=?", (ss["id"],))
            audit(c, "logout", session_id=ss["id"])
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("linda_sid")
    return response


@app.get("/dashboard")
def dashboard(request: Request):
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
        total = c.execute("SELECT COUNT(*) FROM faxes").fetchone()[0]
        routed = c.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
    body = f"""<div class='eyebrow'>Home / Overview</div><h1>Fax Management Workspace</h1>
    <p class='subtitle'>Review inbound transmissions and route documents to the appropriate internal team.</p>
    <div class='metrics'><div class='card'><div class='metriccap'>All inbound</div><div class='metric'>{total}</div></div>
    <div class='card'><div class='metriccap'>Awaiting routing</div><div class='metric'>{total-routed}</div></div>
    <div class='card'><div class='metriccap'>Already routed</div><div class='metric'>{routed}</div></div></div>
    <section class='card'><h2>Fax Management</h2><p class='subtitle'>Open the fax workspace, navigate to Inbound Faxes, then select a transmission to view and download its document.</p>
    <a class='btn' href='/faxes'>Open Fax Management</a></section>"""
    return html("Dashboard", body, ss, "dashboard")


@app.get("/faxes")
def fax_workspace(request: Request):
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
    return html("Fax Management", """<div class='eyebrow'>Workspace / Communications</div><h1>Fax Management</h1>
    <section class='card'><h2>Inbound Faxes</h2><p>View received fax transmissions, open scanned documents, and route them to a staff member, document type and folder.</p>
    <a class='btn' href='/faxes/inbound'>Go to Inbound Faxes</a></section>""", ss, "faxes")


@app.get("/faxes/inbound")
def inbound(request: Request, q: str = "", page: int = 1, status: str = "all"):
    page = max(page, 1)
    if status not in ("all", "new", "routed"): status = "all"
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
        where = ["1=1"]; args = []
        if q:
            where.append("(f.fax_id LIKE ? OR f.sender LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        if status == "new": where.append("r.fax_id IS NULL")
        if status == "routed": where.append("r.fax_id IS NOT NULL")
        predicate = " AND ".join(where)
        total = c.execute(f"SELECT COUNT(*) FROM faxes f LEFT JOIN routes r ON f.fax_id=r.fax_id WHERE {predicate}", args).fetchone()[0]
        rows = c.execute(f"""SELECT f.*,r.routed_at FROM faxes f LEFT JOIN routes r ON f.fax_id=r.fax_id
            WHERE {predicate} ORDER BY f.received_at DESC,f.fax_id DESC LIMIT 6 OFFSET ?""", args + [(page-1)*6]).fetchall()
    table_rows = "".join(
        f"<tr><td><a href='/faxes/{esc(x['fax_id'])}'>{esc(x['fax_id'])}</a></td><td>{esc(x['received_at'])}</td><td>{esc(x['sender'])}</td>"
        f"<td>{x['pages']}</td><td><span class='badge {'done' if x['routed_at'] is not None else 'new'}'>{'Routed' if x['routed_at'] is not None else 'New'}</span></td>"
        f"<td><a href='/faxes/{esc(x['fax_id'])}'>Open</a></td></tr>" for x in rows)
    params = f"&q={quote(q)}&status={quote(status)}"
    last = max(1,(total+5)//6)
    pager = f"<div class='pagination'><span class='small'>Page {page} of {last} · {total} transmissions</span><div class='actions'>"
    pager += (f"<a class='btn secondary' href='/faxes/inbound?page={page-1}{params}'>Previous</a>" if page>1 else "")
    pager += (f"<a class='btn secondary' href='/faxes/inbound?page={page+1}{params}'>Next</a>" if page<last else "") + "</div></div>"
    body = f"""<div class='eyebrow'>Fax Management / Inbound Faxes</div><h1>Inbound Faxes</h1>
    <p class='subtitle'>Incoming transmissions. The inbox does not classify medical document contents.</p>
    <section class='card'><form class='controls' action='/faxes/inbound' method='get'>
      <div><label for='search'>Search fax ID or sender</label><input id='search' name='q' value='{esc(q)}' placeholder='e.g. FX-201' /></div>
      <div><label for='status'>Status</label><select id='status' name='status'><option value='all' {'selected' if status=='all' else ''}>All</option><option value='new' {'selected' if status=='new' else ''}>New</option><option value='routed' {'selected' if status=='routed' else ''}>Routed</option></select></div>
      <button type='submit'>Filter</button><a class='btn secondary' href='/faxes/inbound'>Clear</a>
    </form></section>
    <section class='card' style='padding:0;overflow:hidden'><table aria-label='Inbound faxes'><thead><tr><th>Fax ID</th><th>Received</th><th>From</th><th>Pages</th><th>Status</th><th>Action</th></tr></thead><tbody>{table_rows if table_rows else '<tr><td colspan=6>No faxes match your search.</td></tr>'}</tbody></table></section>{pager}"""
    return html("Inbound Faxes", body, ss, "inbound")


def fax_row(c, fax_id):
    return c.execute("SELECT f.*,r.doc_type,r.assignee,r.folder,r.notes,r.routed_at FROM faxes f LEFT JOIN routes r ON f.fax_id=r.fax_id WHERE f.fax_id=?", (fax_id,)).fetchone()


@app.get("/faxes/{fax_id}")
def fax_detail(request: Request, fax_id: str, notice_type: str = ""):
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
        record = fax_row(c, fax_id)
    if record is None: return html("Not found", notice("This fax does not exist.", "error"), ss, "inbound", 404)
    if record["routed_at"]:
        name = dict(ASSIGNEES).get(record["assignee"], record["assignee"])
        route_panel = f"""{notice('Routing completed. This fax cannot be submitted again.')}
          <dl class='kv'><dt>Assigned To</dt><dd>{esc(name)}</dd><dt>Document Type</dt><dd>{esc(record['doc_type'])}</dd>
          <dt>Document Folder</dt><dd>{esc(record['folder'])}</dd><dt>Routing status</dt><dd>Completed</dd></dl>
          <p class='small'>To verify routing, open the Routed Documents work queue.</p><a class='btn secondary' href='/work-queue'>View work queue</a>"""
    else:
        route_panel = "<p class='subtitle'>After reviewing and downloading the fax, choose the intended staff member, document type and folder.</p>" + f"<a class='btn' href='/faxes/{esc(fax_id)}/route'>Route / Assign Fax</a>"
    messages = {"success":"Fax routing was recorded successfully.","already":"This fax was already routed; no new record was created."}
    flash = notice(messages[notice_type]) if notice_type in messages else ""
    body = f"""<p class='crumb'><a href='/faxes/inbound'>Inbound Faxes</a> / {esc(fax_id)}</p>
    <div class='eyebrow'>Inbound transmission details</div><h1>Fax {esc(fax_id)}</h1>{flash}
    <div class='two'><section class='card'><div class='spaced'><h2>Document preview</h2><span class='badge'>Scanned PDF</span></div>
       <iframe class='preview' title='Scanned fax preview' src='/faxes/{esc(fax_id)}/preview'></iframe>
       <div style='margin-top:14px' class='actions'><a id='download-fax' class='btn' href='/faxes/{esc(fax_id)}/download'>Download Fax PDF</a>
       <a class='btn secondary' target='_blank' rel='noopener' href='/faxes/{esc(fax_id)}/preview'>Open preview</a></div>
    </section><section><div class='card'><h2>Transmission information</h2><dl class='kv'>
      <dt>Fax ID</dt><dd><span class='mono'>{esc(fax_id)}</span></dd><dt>Received</dt><dd>{esc(record['received_at'])}</dd>
      <dt>Sender</dt><dd>{esc(record['sender'])}</dd><dt>Pages</dt><dd>{record['pages']}</dd>
      <dt>Status</dt><dd>{'Routed' if record['routed_at'] else 'New'}</dd></dl></div>
      <div class='card'><h2>Document routing</h2>{route_panel}</div></section></div>"""
    return html(f"Fax {fax_id}", body, ss, "inbound")


@app.get("/faxes/{fax_id}/preview")
def preview(request: Request, fax_id: str):
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
        row = c.execute("SELECT filename FROM faxes WHERE fax_id=?", (fax_id,)).fetchone()
    if not row: return HTMLResponse("Not found", status_code=404)
    return FileResponse(DATA_DIR / row["filename"], media_type="application/pdf", headers={"Content-Disposition":f'inline; filename="{fax_id}.pdf"'})


@app.get("/faxes/{fax_id}/download")
def download(request: Request, fax_id: str):
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
        row = c.execute("SELECT filename FROM faxes WHERE fax_id=?", (fax_id,)).fetchone()
        if not row: return HTMLResponse("Not found", status_code=404)
        if fault_once(c, "download_503_once"):
            audit(c, "download_failed", fax_id, ss["id"], "simulated transient 503")
            return html("Download unavailable", notice("Download temporarily unavailable (503). Retry the action.", "error"), ss, "inbound", 503)
        c.execute("INSERT INTO downloads(fax_id,session_id,downloaded_at) VALUES(?,?,?)", (fax_id, ss["id"], now()))
        audit(c, "download", fax_id, ss["id"])
    return FileResponse(DATA_DIR / row["filename"], media_type="application/pdf", filename=f"{fax_id}.pdf")


@app.get("/faxes/{fax_id}/route")
def route_form(request: Request, fax_id: str):
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
        row = fax_row(c, fax_id)
    if not row: return html("Not found", notice("Fax not found", "error"), ss, "inbound", 404)
    if row["routed_at"]: return RedirectResponse(f"/faxes/{quote(fax_id)}?notice_type=already", status_code=303)
    body = f"""<p class='crumb'><a href='/faxes/inbound'>Inbound Faxes</a> / <a href='/faxes/{esc(fax_id)}'>{esc(fax_id)}</a> / Routing</p>
    <div class='eyebrow'>Fax routing</div><h1>Route fax {esc(fax_id)}</h1>
    <p class='subtitle'>Choose the appropriate values from the actual scanned document. This action sends the fax to the selected internal workflow.</p>
    <div class='two'><section class='card'><h2>Assignment details</h2>
    <form id='route-form' method='post' action='/faxes/{esc(fax_id)}/route'>
      <input type='hidden' name='csrf' value='{esc(ss['csrf'])}'/>
      <label for='assignee'>Assigned To *</label><select id='assignee' name='assignee' required>{options(ASSIGNEES,'Select staff member')}</select>
      <label for='doc_type'>Document Type *</label><select id='doc_type' name='doc_type' required>{options([(x,x) for x in DOCUMENT_TYPES],'Select document type')}</select>
      <label for='folder'>Document Folder *</label><select id='folder' name='folder' required>{options([(x,x) for x in FOLDERS],'Select destination folder')}</select>
      <label for='notes'>Routing note (optional)</label><textarea name='notes' id='notes' rows='3' placeholder='Reason for exception or relevant operational context'></textarea>
      <div style='margin-top:22px' class='actions'><button type='button' onclick='openConfirmation()'>Review Routing</button>
      <a class='btn secondary' href='/faxes/{esc(fax_id)}'>Cancel</a></div>
    </form></section><section class='card'><h2>Source transmission</h2>
    <dl class='kv'><dt>Fax ID</dt><dd>{esc(fax_id)}</dd><dt>Sender</dt><dd>{esc(row['sender'])}</dd>
    <dt>Received</dt><dd>{esc(row['received_at'])}</dd><dt>Pages</dt><dd>{row['pages']}</dd></dl>
    <p class='small'>Use the fax ID to find the same transmission across sessions. The portal does not perform classification.</p>
    <div class='actions'><a class='btn secondary' href='/faxes/{esc(fax_id)}'>View fax</a></div></section></div>
    <div class='modal-backdrop' id='confirmation' role='dialog' aria-modal='true' aria-label='Confirm document routing'>
      <div class='modal'><h2>Confirm routing</h2><p>Verify the assignment below before committing.</p>
      <dl class='kv'><dt>Fax</dt><dd>{esc(fax_id)}</dd><dt>Assigned To</dt><dd id='summary-assignee'></dd>
      <dt>Document Type</dt><dd id='summary-type'></dd><dt>Document Folder</dt><dd id='summary-folder'></dd></dl>
      <div style='margin-top:25px' class='actions'><button id='confirm-ok' type='submit' form='route-form'>OK - Submit Routing</button>
       <button type='button' class='secondary' onclick='document.getElementById("confirmation").classList.remove("show")'>Back</button></div>
      </div></div>
    <script>
      function openConfirmation(){{
        const form=document.getElementById('route-form');
        if(!form.reportValidity())return;
        for(const [field,target] of [['assignee','summary-assignee'],['doc_type','summary-type'],['folder','summary-folder']]){{
          const el=form.elements[field];document.getElementById(target).textContent=el.selectedOptions[0].text;
        }}
        document.getElementById('confirmation').classList.add('show');
      }}
    </script>"""
    return html(f"Route {fax_id}", body, ss, "inbound")


@app.post("/faxes/{fax_id}/route")
def route_save(request: Request, fax_id: str, csrf: str = Form(""), assignee: str = Form(""), doc_type: str = Form(""), folder: str = Form(""), notes: str = Form("")):
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
        if not secrets.compare_digest(csrf, ss["csrf"]):
            return html("Invalid session", notice("Invalid form token. Refresh and try again.", "error"), ss, "inbound", 403)
        if assignee not in dict(ASSIGNEES) or doc_type not in DOCUMENT_TYPES or folder not in FOLDERS:
            return html("Invalid route", notice("Choose valid values in all required fields.", "error"), ss, "inbound", 400)
        row = fax_row(c, fax_id)
        if not row: return html("Not found", notice("Fax not found.", "error"), ss, "inbound", 404)
        if row["routed_at"]:
            audit(c, "repeated_route_blocked", fax_id, ss["id"])
            return RedirectResponse(f"/faxes/{quote(fax_id)}?notice_type=already", status_code=303)
        # Enforce the exercise's two-session contract at the browser portal itself:
        # the PDF must have been downloaded in a DIFFERENT, older login session.
        prior_download = c.execute("""SELECT 1 FROM downloads d
         WHERE d.fax_id=? AND d.session_id<>? AND d.downloaded_at < ? LIMIT 1""",
         (fax_id, ss["id"], ss["created_at"])).fetchone()
        if not prior_download:
            audit(c, "two_session_rule_blocked", fax_id, ss["id"])
            return html("Separate session required", notice("Routing requires a prior download in an earlier, separate login session. Start a new browser session, log in, and find this fax again.", "warning"), ss, "inbound", 409)
        if fault_once(c, "session_expire_on_route_once"):
            c.execute("DELETE FROM sessions WHERE id=?", (ss["id"],))
            audit(c, "session_expired", fax_id, ss["id"], "simulated expiration on submit")
            response = RedirectResponse("/login?next=" + quote(request.url.path, safe=""), status_code=303)
            response.delete_cookie("linda_sid")
            return response
        # Lock the DB writer and recheck to prevent concurrent duplicate routes.
        c.execute("BEGIN IMMEDIATE")
        previous = c.execute("SELECT 1 FROM routes WHERE fax_id=?", (fax_id,)).fetchone()
        if previous:
            audit(c, "repeated_route_blocked", fax_id, ss["id"])
            return RedirectResponse(f"/faxes/{quote(fax_id)}?notice_type=already", status_code=303)
        c.execute("""INSERT INTO routes(fax_id,doc_type,assignee,folder,notes,session_id,routed_at)
          VALUES(?,?,?,?,?,?,?)""", (fax_id, doc_type, assignee, folder, notes[:1000], ss["id"], now()))
        audit(c, "route_committed", fax_id, ss["id"], f"{doc_type} | {assignee} | {folder}")
        c.commit()
        if fault_once(c, "route_commit_then_503_once"):
            audit(c, "response_lost", fax_id, ss["id"], "route persisted, response 503")
            return html("Routing unavailable", notice("Gateway error (503). The outcome of this submit is unknown; check the fax's current status before retrying.", "error"), ss, "inbound", 503)
        return RedirectResponse(f"/faxes/{quote(fax_id)}?notice_type=success", status_code=303)


@app.get("/work-queue")
def work_queue(request: Request):
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
        rows = c.execute("SELECT f.fax_id, f.sender, r.doc_type,r.assignee,r.folder,r.routed_at FROM routes r JOIN faxes f ON r.fax_id=f.fax_id ORDER BY r.routed_at DESC").fetchall()
    labels = dict(ASSIGNEES)
    trs = "".join(f"<tr><td><a href='/faxes/{esc(r['fax_id'])}'>{esc(r['fax_id'])}</a></td>"
        f"<td>{esc(r['doc_type'])}</td><td>{esc(labels.get(r['assignee'],r['assignee']))}</td>"
        f"<td>{esc(r['folder'])}</td><td><span class='badge done'>Routed</span></td></tr>" for r in rows)
    body = f"""<div class='eyebrow'>Fax Management / Routed Documents</div><h1>Routed Documents</h1>
    <p class='subtitle'>Completed assignments. Each fax can have at most one routing record.</p>
    <section class='card' style='padding:0;overflow:hidden'><table aria-label='Routed documents'><thead><tr>
    <th>Fax ID</th><th>Document Type</th><th>Assigned To</th><th>Document Folder</th><th>Status</th></tr></thead>
    <tbody>{trs}</tbody></table></section>"""
    return html("Routed Documents", body, ss, "queue")


@app.get("/folders")
def folders(request: Request):
    with conn() as c:
        ss, redirect = require(request, c)
        if redirect: return redirect
    contents = "".join(f"<tr><td>{esc(folder)}</td></tr>" for folder in FOLDERS)
    return html("Document Folders", f"<div class='eyebrow'>Document Management</div><h1>Document Folders</h1><p class='subtitle'>Available routing destinations.</p><section class='card'><table><thead><tr><th>Folder name</th></tr></thead><tbody>{contents}</tbody></table></section>", ss, "folders")


@app.get("/__admin/state")
def admin_state(token: str = ""):
    if not secrets.compare_digest(token, ADMIN_TOKEN):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    with conn() as c:
        faxes = [dict(x) for x in c.execute("SELECT f.fax_id,f.received_at,r.doc_type,r.assignee,r.folder,r.notes,r.routed_at,r.session_id AS routing_session FROM faxes f LEFT JOIN routes r ON r.fax_id=f.fax_id ORDER BY f.fax_id")]
        downloads = [dict(x) for x in c.execute("SELECT fax_id,session_id,downloaded_at FROM downloads ORDER BY id")]
        sessions = [dict(x) for x in c.execute("SELECT id,username,created_at FROM sessions ORDER BY created_at")]
        audits = [dict(x) for x in c.execute("SELECT event,fax_id,session_id,detail,at FROM audit_events ORDER BY id")]
    return {"faxes":faxes,"downloads":downloads,"sessions":sessions,"audit_events":audits,"fault_mode":FAULT_MODE}


@app.post("/__admin/reset")
def admin_reset(token: str = ""):
    if not secrets.compare_digest(token, ADMIN_TOKEN):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    with conn() as c:
        for table in ["audit_events","routes","downloads","sessions","fault_events","faxes"]:
            c.execute(f"DELETE FROM {table}")
    init_db()
    return {"status":"reset","next":"/login"}
