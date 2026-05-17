import os
import asyncio
import threading
from flask import Flask, request, jsonify, render_template_string, send_from_path
from torrentp import TorrentDownloader

app = Flask(__name__)

# Directory where torrents will save inside the container
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Tracks download instances globally
active_downloads = {}

# Simple embedded UI layout
HTML_TEMPLATE = """
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

def run_torrent_async(magnet_or_url):
    """Worker function to run the async torrent loop in a background thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        downloader = TorrentDownloader(magnet_or_url, DOWNLOAD_DIR)
        active_downloads[magnet_or_url] = downloader
        # Start the asynchronous download process natively supported by torrentp
        loop.run_until_complete(downloader.start_download())
    except Exception as e:
        print(f"Error downloading torrent: {e}")

@app.route('/')
def home():
    # List files available in the container storage
    files = os.listdir(DOWNLOAD_DIR)
    return render_template_string(HTML_TEMPLATE, files=files)

@app.route('/download', methods=['POST'])
def start_download():
    torrent_url = request.form.get('torrent_url')
    if not torrent_url:
        return jsonify({"error": "No link provided"}), 400

    # Offload the blocking/async download activity to a background thread
    thread = threading.Thread(target=run_torrent_async, args=(torrent_url,))
    thread.start()

    return jsonify({"status": "Download started in background. Refresh the home page shortly."}), 200

@app.route('/files/<path:filename>')
def download_file(filename):
    # Route to securely pull completed files back to your computer
    return send_from_path(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    # Railway passes a dynamic $PORT environment variable. Your app MUST bind to it.
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
