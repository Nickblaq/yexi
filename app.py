# app.py — Torrent Downloader
# libtorrent 2.0.x | FastAPI | single-file

import os
import time
import uuid
import threading
from pathlib import Path
from typing import Dict, Optional

import libtorrent as lt
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────

DOWNLOADS_DIR = Path(os.environ.get("DOWNLOADS_DIR", "./downloads"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# ── State ─────────────────────────────────────────────

# torrent_id -> { handle, status_dict }
torrent_store: Dict[str, dict] = {}
store_lock = threading.Lock()

# ── libtorrent session (singleton) ────────────────────

def make_session() -> lt.session:
    settings = {
        "listen_interfaces": "0.0.0.0:6881",
        "enable_dht": True,
        "enable_lsd": True,
        "enable_upnp": True,
        "enable_natpmp": True,
    }
    ses = lt.session(settings)
    ses.add_dht_router("router.bittorrent.com", 6881)
    ses.add_dht_router("router.utorrent.com", 6881)
    ses.add_dht_router("dht.transmissionbt.com", 6881)
    return ses

SESSION = make_session()

# ── Background poller ─────────────────────────────────

STATE_LABELS = [
    "queued", "checking", "downloading metadata",
    "downloading", "finished", "seeding",
    "checking resume data", "unknown"
]

def _poll_loop():
    """Continuously update torrent_store with live status."""
    while True:
        with store_lock:
            for tid, entry in list(torrent_store.items()):
                h: lt.torrent_handle = entry.get("handle")
                if h is None or not h.is_valid():
                    continue
                s = h.status()
                state_idx = int(s.state)
                state_label = STATE_LABELS[state_idx] if state_idx < len(STATE_LABELS) else "unknown"
                entry["progress"]      = round(s.progress * 100, 2)
                entry["state"]         = state_label
                entry["down_rate"]     = round(s.download_rate / 1024, 1)   # KB/s
                entry["up_rate"]       = round(s.upload_rate / 1024, 1)     # KB/s
                entry["peers"]         = s.num_peers
                entry["name"]          = s.name or entry.get("name", "")
                entry["total_size"]    = s.total_wanted
                entry["downloaded"]    = s.total_wanted_done
                if state_label in ("finished", "seeding") and entry.get("status") != "completed":
                    entry["status"] = "completed"
                    # record all downloaded files
                    try:
                        ti = h.torrent_file()
                        if ti:
                            files = []
                            for i in range(ti.num_files()):
                                fp = DOWNLOADS_DIR / ti.files().file_path(i)
                                if fp.exists():
                                    files.append(str(fp.relative_to(DOWNLOADS_DIR)))
                            entry["files"] = files
                        else:
                            entry["files"] = []
                    except Exception:
                        entry["files"] = []
                elif entry.get("status") not in ("completed", "error"):
                    entry["status"] = "downloading"
        time.sleep(1)

threading.Thread(target=_poll_loop, daemon=True).start()

# ── FastAPI ───────────────────────────────────────────

app = FastAPI(title="Torrent Downloader")

class AddRequest(BaseModel):
    magnet: str

# ── Helpers ───────────────────────────────────────────

def _fmt_size(b: int) -> str:
    if b <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"

# ── API ───────────────────────────────────────────────

@app.post("/add")
def add_torrent(req: AddRequest):
    magnet = req.magnet.strip()
    if not magnet.startswith("magnet:"):
        raise HTTPException(400, "Only magnet links are supported")

    torrent_id = str(uuid.uuid4())

    try:
        # libtorrent 2.0 correct API: parse_magnet_uri → set save_path → add_torrent
        params = lt.parse_magnet_uri(magnet)
        params.save_path = str(DOWNLOADS_DIR)
        handle = SESSION.add_torrent(params)
    except Exception as e:
        raise HTTPException(500, f"Failed to add torrent: {e}")

    with store_lock:
        torrent_store[torrent_id] = {
            "handle":   handle,
            "status":   "downloading",
            "progress": 0,
            "state":    "downloading metadata",
            "down_rate": 0,
            "up_rate":   0,
            "peers":     0,
            "name":      "",
            "total_size": 0,
            "downloaded": 0,
            "files":     [],
            "magnet":    magnet,
        }

    return {"torrent_id": torrent_id}


@app.get("/status/{torrent_id}")
def get_status(torrent_id: str):
    with store_lock:
        entry = torrent_store.get(torrent_id)
    if not entry:
        raise HTTPException(404, "Torrent not found")
    return {
        "status":     entry["status"],
        "progress":   entry["progress"],
        "state":      entry["state"],
        "down_rate":  entry["down_rate"],
        "up_rate":    entry["up_rate"],
        "peers":      entry["peers"],
        "name":       entry["name"],
        "total_size": _fmt_size(entry["total_size"]),
        "downloaded": _fmt_size(entry["downloaded"]),
        "files":      entry.get("files", []),
    }


@app.get("/files")
def list_files():
    """Return all files currently in DOWNLOADS_DIR."""
    results = []
    for p in sorted(DOWNLOADS_DIR.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(DOWNLOADS_DIR))
            results.append({
                "name": rel,
                "size": _fmt_size(p.stat().st_size),
            })
    return {"files": results}


@app.get("/download-file")
def download_file(path: str):
    """Serve a file from DOWNLOADS_DIR by its relative path."""
    target = (DOWNLOADS_DIR / path).resolve()
    # Security: ensure path stays inside DOWNLOADS_DIR
    if not str(target).startswith(str(DOWNLOADS_DIR.resolve())):
        raise HTTPException(403, "Forbidden")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


@app.get("/health")
def health():
    return {"status": "ok"}


# ── UI ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>TORRENT // DROP</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

:root{
  --bg:     #0a0a0a;
  --bg2:    #111111;
  --bg3:    #181818;
  --border: #222222;
  --border2:#2e2e2e;
  --accent: #e8ff47;
  --accent2:#b8cc2a;
  --red:    #ff4545;
  --green:  #47ff8e;
  --blue:   #47b4ff;
  --text:   #c8c8c8;
  --dim:    #505050;
  --white:  #f0f0f0;
  --mono:   'JetBrains Mono', monospace;
  --display:'Syne', sans-serif;
}

html,body{min-height:100%;background:var(--bg);color:var(--text);font-family:var(--display);overflow-x:hidden}

/* noise grain */
body::after{
  content:'';position:fixed;inset:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
  pointer-events:none;z-index:9998;opacity:.4;
}

.shell{max-width:820px;margin:0 auto;padding:48px 24px 80px}

/* ── header ── */
.header{margin-bottom:48px}
.logo{font-size:52px;font-weight:800;letter-spacing:-0.02em;color:var(--white);line-height:1}
.logo span{color:var(--accent)}
.tagline{font-family:var(--mono);font-size:11px;color:var(--dim);letter-spacing:.14em;text-transform:uppercase;margin-top:8px}
.header-line{height:1px;background:linear-gradient(90deg,var(--accent) 0%,transparent 60%);margin-top:20px}

/* ── section label ── */
.section-label{
  font-family:var(--mono);font-size:9px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--dim);margin-bottom:12px;
  display:flex;align-items:center;gap:10px
}
.section-label::after{content:'';flex:1;height:1px;background:var(--border)}

/* ── input block ── */
.input-block{margin-bottom:32px}
.input-row{display:flex;gap:0;border:1px solid var(--border2);border-radius:3px;overflow:hidden;transition:border-color .2s}
.input-row:focus-within{border-color:var(--accent)}
.input-prefix{
  font-family:var(--mono);font-size:10px;color:var(--accent);
  background:rgba(232,255,71,.05);border-right:1px solid var(--border2);
  padding:0 14px;display:flex;align-items:center;white-space:nowrap;
  letter-spacing:.06em;user-select:none
}
#magnet-input{
  flex:1;background:transparent;border:none;outline:none;
  color:var(--white);font-family:var(--mono);font-size:12px;
  padding:14px 16px;min-width:0
}
#magnet-input::placeholder{color:var(--dim)}
.btn-add{
  font-family:var(--mono);font-size:11px;font-weight:600;
  letter-spacing:.1em;text-transform:uppercase;border:none;
  background:var(--accent);color:#000;padding:0 24px;cursor:pointer;
  transition:background .15s;white-space:nowrap;flex-shrink:0
}
.btn-add:hover{background:var(--accent2)}
.btn-add:disabled{background:#3a3d00;color:#6a6f00;cursor:not-allowed}
.status-msg{font-family:var(--mono);font-size:10px;color:var(--dim);margin-top:8px;min-height:16px}
.status-msg.err{color:var(--red)}

/* ── active downloads ── */
.torrents-block{margin-bottom:36px}
.torrent-card{
  background:var(--bg2);border:1px solid var(--border);border-radius:3px;
  overflow:hidden;margin-bottom:10px;position:relative
}
.torrent-card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,var(--accent),transparent 70%)
}
.tc-header{padding:12px 16px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--border)}
.tc-name{
  font-family:var(--mono);font-size:12px;color:var(--white);
  flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap
}
.tc-state{
  font-family:var(--mono);font-size:9px;letter-spacing:.1em;
  text-transform:uppercase;padding:3px 8px;border-radius:2px;flex-shrink:0
}
.tc-state.downloading{background:rgba(71,180,255,.1);color:var(--blue);border:1px solid rgba(71,180,255,.2)}
.tc-state.seeding,.tc-state.completed{background:rgba(71,255,142,.1);color:var(--green);border:1px solid rgba(71,255,142,.2)}
.tc-state.checking,.tc-state.queued{background:rgba(232,255,71,.07);color:var(--accent);border:1px solid rgba(232,255,71,.15)}
.tc-state.error{background:rgba(255,69,69,.1);color:var(--red);border:1px solid rgba(255,69,69,.2)}

.tc-body{padding:12px 16px}
.tc-track{width:100%;height:3px;background:var(--border2);border-radius:0;margin-bottom:12px;overflow:hidden}
.tc-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--green));transition:width .6s ease;border-radius:0}
.tc-meta{display:flex;gap:20px;flex-wrap:wrap}
.tc-stat{display:flex;flex-direction:column;gap:2px}
.tc-stat-key{font-family:var(--mono);font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:.1em}
.tc-stat-val{font-family:var(--mono);font-size:13px;color:var(--white);font-weight:600}
.tc-pct{font-family:'Syne',sans-serif;font-size:28px;font-weight:800;color:var(--accent);line-height:1;margin-left:auto;align-self:center}

/* ── files table ── */
.files-block{margin-bottom:36px}
.files-table{width:100%;border-collapse:collapse}
.files-table th{
  font-family:var(--mono);font-size:9px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--dim);text-align:left;padding:8px 12px;
  border-bottom:1px solid var(--border);background:var(--bg3)
}
.files-table td{
  font-family:var(--mono);font-size:11px;color:var(--text);
  padding:10px 12px;border-bottom:1px solid var(--border);
  vertical-align:middle
}
.files-table tr:last-child td{border-bottom:none}
.files-table tr:hover td{background:rgba(255,255,255,.02)}
.td-name{color:var(--white);word-break:break-all}
.td-size{color:var(--dim);white-space:nowrap}
.btn-dl{
  font-family:var(--mono);font-size:9px;letter-spacing:.1em;text-transform:uppercase;
  background:transparent;border:1px solid var(--border2);color:var(--accent);
  padding:5px 12px;border-radius:2px;cursor:pointer;transition:all .15s;white-space:nowrap
}
.btn-dl:hover{background:rgba(232,255,71,.07);border-color:var(--accent)}
.empty-state{
  font-family:var(--mono);font-size:11px;color:var(--dim);
  text-align:center;padding:32px;letter-spacing:.06em
}

/* ── spinner ── */
.spinner{
  display:inline-block;width:10px;height:10px;
  border:1.5px solid var(--border2);border-top-color:var(--accent);
  border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:6px
}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── anim ── */
@keyframes fadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.anim{animation:fadeUp .3s ease forwards}
</style>
</head>
<body>
<div class="shell">

  <!-- Header -->
  <header class="header anim">
    <div class="logo">TORR<span>.</span>DROP</div>
    <div class="tagline">BitTorrent acquisition // libtorrent 2.0</div>
    <div class="header-line"></div>
  </header>

  <!-- Add torrent -->
  <div class="input-block anim">
    <div class="section-label">Add torrent</div>
    <div class="input-row">
      <div class="input-prefix">MAGNET://</div>
      <input id="magnet-input" type="text" placeholder="magnet:?xt=urn:btih:…" spellcheck="false" autocomplete="off"/>
      <button class="btn-add" id="btn-add" onclick="addTorrent()">Add</button>
    </div>
    <div class="status-msg" id="add-msg"></div>
  </div>

  <!-- Active downloads -->
  <div class="torrents-block">
    <div class="section-label">Active downloads</div>
    <div id="torrents-list">
      <div class="empty-state" id="no-downloads">No active downloads</div>
    </div>
  </div>

  <!-- Files in download dir -->
  <div class="files-block">
    <div class="section-label">Downloaded files</div>
    <div id="files-container">
      <div class="empty-state" id="no-files">No files yet</div>
    </div>
  </div>

</div>

<script>
// torrent_id -> interval_id
const activePollers = {};

function setMsg(msg, isErr=false) {
  const el = document.getElementById('add-msg');
  el.textContent = msg;
  el.className = 'status-msg' + (isErr ? ' err' : '');
}

async function addTorrent() {
  const input = document.getElementById('magnet-input');
  const magnet = input.value.trim();
  if (!magnet) { setMsg('Paste a magnet link first.', true); return; }
  if (!magnet.startsWith('magnet:')) { setMsg('Only magnet:// links are supported.', true); return; }

  const btn = document.getElementById('btn-add');
  btn.disabled = true;
  setMsg('<span class="spinner"></span> Adding…');
  document.getElementById('add-msg').innerHTML = '<span class="spinner"></span> Adding…';

  try {
    const res = await fetch('/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({magnet})
    });
    const data = await res.json();
    if (!res.ok) { setMsg(data.detail || 'Failed to add torrent.', true); return; }

    input.value = '';
    setMsg('Torrent added — tracking…');
    document.getElementById('no-downloads').style.display = 'none';
    createCard(data.torrent_id);
    startPolling(data.torrent_id);
  } catch(e) {
    setMsg('Network error: ' + e.message, true);
  } finally {
    btn.disabled = false;
  }
}

function createCard(tid) {
  const list = document.getElementById('torrents-list');
  const card = document.createElement('div');
  card.className = 'torrent-card anim';
  card.id = 'card-' + tid;
  card.innerHTML = `
    <div class="tc-header">
      <div class="tc-name" id="name-${tid}">Fetching metadata…</div>
      <div class="tc-state downloading" id="state-${tid}">connecting</div>
    </div>
    <div class="tc-body">
      <div class="tc-track"><div class="tc-fill" id="fill-${tid}" style="width:0%"></div></div>
      <div class="tc-meta">
        <div class="tc-stat">
          <div class="tc-stat-key">Down</div>
          <div class="tc-stat-val" id="down-${tid}">— KB/s</div>
        </div>
        <div class="tc-stat">
          <div class="tc-stat-key">Up</div>
          <div class="tc-stat-val" id="up-${tid}">— KB/s</div>
        </div>
        <div class="tc-stat">
          <div class="tc-stat-key">Peers</div>
          <div class="tc-stat-val" id="peers-${tid}">0</div>
        </div>
        <div class="tc-stat">
          <div class="tc-stat-key">Size</div>
          <div class="tc-stat-val" id="size-${tid}">—</div>
        </div>
        <div class="tc-pct" id="pct-${tid}">0%</div>
      </div>
    </div>
  `;
  list.appendChild(card);
}

function startPolling(tid) {
  const interval = setInterval(async () => {
    try {
      const res = await fetch('/status/' + tid);
      if (!res.ok) return;
      const d = await res.json();
      updateCard(tid, d);
      if (d.status === 'completed') {
        clearInterval(activePollers[tid]);
        delete activePollers[tid];
        refreshFiles();
      }
    } catch(e) {}
  }, 1200);
  activePollers[tid] = interval;
}

function updateCard(tid, d) {
  const stateClass = d.state.includes('seed') || d.status === 'completed'
    ? 'completed' : d.state.includes('check') ? 'checking'
    : d.state.includes('queue') ? 'queued'
    : d.status === 'error' ? 'error' : 'downloading';

  const nameEl = document.getElementById('name-' + tid);
  if (nameEl && d.name) nameEl.textContent = d.name;

  const stateEl = document.getElementById('state-' + tid);
  if (stateEl) { stateEl.textContent = d.state; stateEl.className = 'tc-state ' + stateClass; }

  const fillEl = document.getElementById('fill-' + tid);
  if (fillEl) fillEl.style.width = d.progress + '%';

  const pctEl = document.getElementById('pct-' + tid);
  if (pctEl) pctEl.textContent = Math.round(d.progress) + '%';

  const downEl = document.getElementById('down-' + tid);
  if (downEl) downEl.textContent = d.down_rate + ' KB/s';

  const upEl = document.getElementById('up-' + tid);
  if (upEl) upEl.textContent = d.up_rate + ' KB/s';

  const peersEl = document.getElementById('peers-' + tid);
  if (peersEl) peersEl.textContent = d.peers;

  const sizeEl = document.getElementById('size-' + tid);
  if (sizeEl) sizeEl.textContent = d.total_size || '—';
}

async function refreshFiles() {
  try {
    const res = await fetch('/files');
    const data = await res.json();
    const container = document.getElementById('files-container');
    const noFiles = document.getElementById('no-files');

    if (!data.files || data.files.length === 0) {
      container.innerHTML = '<div class="empty-state" id="no-files">No files yet</div>';
      return;
    }

    noFiles && (noFiles.style.display = 'none');

    let rows = data.files.map(f => `
      <tr>
        <td class="td-name">${escHtml(f.name)}</td>
        <td class="td-size">${escHtml(f.size)}</td>
        <td style="text-align:right">
          <button class="btn-dl" onclick="downloadFile('${escAttr(f.name)}')">Download</button>
        </td>
      </tr>
    `).join('');

    container.innerHTML = `
      <table class="files-table">
        <thead><tr>
          <th>File</th><th>Size</th><th style="text-align:right">Action</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  } catch(e) {}
}

function downloadFile(path) {
  window.location.href = '/download-file?path=' + encodeURIComponent(path);
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function escAttr(s) {
  return s.replace(/'/g, "\\'");
}

// Enter key to add
document.getElementById('magnet-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') addTorrent();
});

// Poll files every 5s regardless
setInterval(refreshFiles, 5000);
refreshFiles();
</script>
</body>
</html>"""
