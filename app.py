import os
import asyncio
import threading
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
import uvicorn

from torrentp import TorrentDownloader

app = FastAPI(title="Cloud Torrent Downloader")

# Safe, absolute path to the downloads folder
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
os.chmod(DOWNLOAD_DIR, 0o777)  # ensure write permissions

# Keep track of active downloads simply for logging/status
active_downloads = {}

# ---------------------------------------------------------------------------
# Background worker – downloads the torrent and prints detailed logs
# ---------------------------------------------------------------------------
def run_torrent_async(magnet_or_url: str, download_id: str):
    print(f"[{download_id}] Starting download for: {magnet_or_url}")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        downloader = TorrentDownloader(magnet_or_url, str(DOWNLOAD_DIR))
        active_downloads[download_id] = "downloading"
        print(f"[{download_id}] Download in progress...")
        loop.run_until_complete(downloader.start_download())

        # Find the newest file – the one just created by the torrent
        all_files = sorted(
            DOWNLOAD_DIR.iterdir(),
            key=lambda f: f.stat().st_ctime,
            reverse=True,
        )
        newest = all_files[0] if all_files else None
        if newest and newest.is_file():
            print(f"[{download_id}] Download completed. File saved as: {newest.name}")
            active_downloads[download_id] = "completed"
        else:
            print(f"[{download_id}] Download finished but no file found.")
            active_downloads[download_id] = "error: no file found"
    except Exception as e:
        err_msg = traceback.format_exc()
        print(f"[{download_id}] ERROR: {type(e).__name__}: {e}")
        print(err_msg)
        active_downloads[download_id] = f"error: {e}"

# ---------------------------------------------------------------------------
# Embedded HTML/JS frontend
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
        #status { margin: 10px 0; color: #555; }
    </style>
</head>
<body>
    <h2>Cloud Torrent Downloader</h2>
    <input type="text" id="torrent_url" placeholder="Paste Magnet Link or Torrent URL here" required>
    <button onclick="startDownload()">Start Download</button>
    <p id="status"></p>
    <div class="file-list">
        <h3>Completed Downloads</h3>
        <ul id="file-list"><li>No files downloaded yet.</li></ul>
        <button onclick="loadFileList()">Refresh list</button>
    </div>

<script>
async function startDownload() {
    const url = document.getElementById("torrent_url").value.trim();
    if (!url) return;

    document.getElementById("status").innerText = "Starting download...";
    const res = await fetch("/download", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "torrent_url=" + encodeURIComponent(url)
    });
    const data = await res.json();
    if (data.status === "started") {
        document.getElementById("status").innerText = "Download started. The file list will refresh automatically every 3 seconds.";
        // Poll file list every 3 seconds
        const interval = setInterval(async () => {
            await loadFileList();
            // If a download completed recently, we could stop polling, but let's keep it simple
        }, 3000);
        // Store interval ID to clear later if needed (optional)
        window.pollInterval = interval;
    } else {
        document.getElementById("status").innerText = "Error: " + (data.error || "Unknown");
    }
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

// Load file list on page load
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

    download_id = uuid.uuid4().hex[:8]  # short ID for logs
    active_downloads[download_id] = "starting"

    thread = threading.Thread(
        target=run_torrent_async, args=(torrent_url, download_id), daemon=True
    )
    thread.start()

    return {"status": "started", "id": download_id}

@app.get("/files")
async def list_files():
    files = sorted([f.name for f in DOWNLOAD_DIR.iterdir() if f.is_file()])
    return files

@app.get("/files/{filename}")
async def download_file(filename: str):
    file_path = DOWNLOAD_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("[INFO] Starting server – downloads folder:", DOWNLOAD_DIR)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
