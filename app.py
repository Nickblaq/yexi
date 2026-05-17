import os
import asyncio
import threading
from pathlib import Path
from typing import List

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles  # not needed but can serve files directly
import uvicorn

from torrentp import TorrentDownloader

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Cloud Torrent Downloader")

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# In‑memory status tracking (simple dict of active/finished downloads)
active_downloads = {}

# ---------------------------------------------------------------------------
# Embedded HTML frontend
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
    </style>
</head>
<body>
    <h2>Cloud Torrent Downloader</h2>
    <form action="/download" method="POST">
        <input type="text" name="torrent_url" placeholder="Paste Magnet Link or Torrent URL here" required>
        <button type="submit">Start Download</button>
    </form>

    <div class="file-list">
        <h3>Completed Downloads</h3>
        <ul>
            {% for file in files %}
                <li><a href="/files/{{ file }}" download>{{ file }}</a></li>
            {% else %}
                <li>No files downloaded yet.</li>
            {% endfor %}
        </ul>
    </div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------
def run_torrent_async(magnet_or_url: str, download_id: str):
    """Run the torrent download in a background thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        downloader = TorrentDownloader(magnet_or_url, str(DOWNLOAD_DIR))
        active_downloads[download_id] = "downloading"
        loop.run_until_complete(downloader.start_download())
        active_downloads[download_id] = "completed"
        print(f"[INFO] Download completed: {magnet_or_url}")
    except Exception as e:
        active_downloads[download_id] = f"error: {e}"
        print(f"[ERROR] {download_id}: {e}")

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home():
    # List files in download directory
    files = sorted([f.name for f in DOWNLOAD_DIR.iterdir() if f.is_file()])
    # Simple templating with string replacement (no Jinja2 needed)
    file_items = "\n".join(
        f'<li><a href="/files/{f}" download>{f}</a></li>' for f in files
    ) if files else "<li>No files downloaded yet.</li>"
    return HTML.replace("{% for file in files %} ... {% endfor %}", file_items, 1) \
               .replace("{% else %}", "").replace("{% endfor %}", "")

@app.post("/download")
async def start_download(torrent_url: str = Form(...)):
    if not torrent_url:
        return {"error": "No link provided"}, 400

    # Generate a simple download ID
    download_id = f"torrent_{len(active_downloads)}"
    active_downloads[download_id] = "starting"

    thread = threading.Thread(target=run_torrent_async, args=(torrent_url, download_id))
    thread.start()

    return {"status": "Download started in background. Refresh the home page to see progress.", "id": download_id}

@app.get("/files/{filename}")
async def download_file(filename: str):
    file_path = DOWNLOAD_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        return {"error": "File not found"}, 404
    return FileResponse(path=str(file_path), filename=filename, media_type="application/octet-stream")

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
