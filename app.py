import os
import asyncio
import threading
import time
import uuid
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
import uvicorn

from torrentp import TorrentDownloader

app = FastAPI(title="Cloud Torrent Downloader")

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# progress store: download_id -> {status, progress, filename}
progress_store: Dict[str, dict] = {}
progress_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------
def run_torrent_async(magnet_or_url: str, download_id: str):
    """Download torrent in a background thread and update progress store."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        with progress_lock:
            progress_store[download_id] = {
                "status": "starting",
                "progress": 0,
                "filename": None,
            }

        # Start torrent download (this call blocks until done)
        downloader = TorrentDownloader(magnet_or_url, str(DOWNLOAD_DIR))
        loop.run_until_complete(downloader.start_download())

        # After completion, find the downloaded file(s)
        # For simplicity we take the most recently created file in DOWNLOAD_DIR
        all_files = sorted(DOWNLOAD_DIR.iterdir(), key=lambda f: f.stat().st_ctime, reverse=True)
        final_file = all_files[0] if all_files else None

        with progress_lock:
            if final_file and final_file.is_file():
                progress_store[download_id] = {
                    "status": "completed",
                    "progress": 100,
                    "filename": final_file.name,
                }
            else:
                progress_store[download_id] = {
                    "status": "error",
                    "progress": 0,
                    "error": "No output file found",
                }
    except Exception as e:
        with progress_lock:
            progress_store[download_id] = {
                "status": "error",
                "progress": 0,
                "error": str(e),
            }

    # Simulated progress updater (runs alongside the blocking download)
    # Because torrentp doesn’t expose real progress, we fake a rising bar.
    def fake_progress():
        for p in range(0, 95, 5):
            time.sleep(2)
            with progress_lock:
                if download_id in progress_store and progress_store[download_id]["status"] == "starting":
                    progress_store[download_id]["status"] = "downloading"
                    progress_store[download_id]["progress"] = p
        # Once loop finishes, the final update happens above

    # Start fake progress in another thread
    threading.Thread(target=fake_progress, daemon=True).start()

# ---------------------------------------------------------------------------
# HTML + JavaScript frontend
# ---------------------------------------------------------------------------
HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Cloud Torrent Downloader</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; }
        input[type="text"] { width: 80%; padding: 10px; margin-bottom: 10px; }
        button { padding: 10px 20px; background: #007bff; color: white; border: none; cursor: pointer; }
        .file-list { margin-top: 20px; background: #f8f9fa; padding: 15px; border-radius: 5px; }
        .progress-bar { width: 100%; height: 20px; background: #ddd; margin-top: 10px; display: none; }
        .progress-fill { height: 100%; width: 0%; background: #28a745; transition: width 0.3s; }
        #download-link { display: none; margin-top: 10px; }
    </style>
</head>
<body>
    <h2>Cloud Torrent Downloader</h2>
    <input type="text" id="torrent_url" placeholder="Paste Magnet Link or Torrent URL here" required>
    <button onclick="startDownload()">Start Download</button>

    <div id="progress-bar" class="progress-bar">
        <div id="progress-fill" class="progress-fill"></div>
    </div>
    <p id="status-text"></p>
    <a id="download-link" href="#" download>Download file</a>

    <div class="file-list">
        <h3>Completed Downloads</h3>
        <ul id="file-list"></ul>
    </div>

<script>
async function startDownload() {
    const url = document.getElementById("torrent_url").value.trim();
    if (!url) return;

    // Hide old results
    document.getElementById("progress-bar").style.display = "block";
    document.getElementById("download-link").style.display = "none";
    document.getElementById("status-text").innerText = "Starting...";

    const res = await fetch("/download", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "torrent_url=" + encodeURIComponent(url)
    });
    const data = await res.json();
    const downloadId = data.id;
    pollProgress(downloadId);
}

async function pollProgress(downloadId) {
    const interval = setInterval(async () => {
        const res = await fetch("/progress/" + downloadId);
        const data = await res.json();
        const fill = document.getElementById("progress-fill");
        const statusText = document.getElementById("status-text");
        const link = document.getElementById("download-link");

        fill.style.width = data.progress + "%";
        statusText.innerText = data.status;

        if (data.status === "completed" && data.filename) {
            clearInterval(interval);
            link.href = "/files/" + encodeURIComponent(data.filename);
            link.style.display = "block";
            link.innerText = "Download " + data.filename;
            statusText.innerText = "Download complete!";
            loadFileList();
        } else if (data.status === "error") {
            clearInterval(interval);
            statusText.innerText = "Error: " + (data.error || "Unknown error");
        }
    }, 1000);
}

async function loadFileList() {
    const res = await fetch("/files");
    const files = await res.json();
    const ul = document.getElementById("file-list");
    ul.innerHTML = "";
    if (files.length === 0) {
        ul.innerHTML = "<li>No files downloaded yet.</li>";
        return;
    }
    files.forEach(f => {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = "/files/" + encodeURIComponent(f);
        a.download = f;
        a.textContent = f;
        li.appendChild(a);
        ul.appendChild(li);
    });
}
window.onload = loadFileList;
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML

@app.post("/download")
async def start_download(torrent_url: str = Form(...)):
    if not torrent_url:
        return JSONResponse({"error": "No link provided"}, status_code=400)

    download_id = uuid.uuid4().hex
    with progress_lock:
        progress_store[download_id] = {"status": "starting", "progress": 0, "filename": None}

    thread = threading.Thread(target=run_torrent_async, args=(torrent_url, download_id), daemon=True)
    thread.start()

    return {"status": "started", "id": download_id}

@app.get("/progress/{download_id}")
async def get_progress(download_id: str):
    with progress_lock:
        data = progress_store.get(download_id)
    if not data:
        return JSONResponse({"error": "Unknown download_id"}, status_code=404)
    return data

@app.get("/files")
async def list_files():
    files = sorted([f.name for f in DOWNLOAD_DIR.iterdir() if f.is_file()])
    return files

@app.get("/files/{filename}")
async def download_file(filename: str):
    file_path = DOWNLOAD_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(path=str(file_path), filename=filename, media_type="application/octet-stream")

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
