
# yexi

A lean, purpose-built HTTP server for YouTube video metadata and downloads.  
Built on `youtube-dl-exec` (yt-dlp binary), Express, and Server-Sent Events.

-----

## Stack

|Layer   |Choice            |Why                                           |
|--------|------------------|----------------------------------------------|
|HTTP    |Express           |Minimal, stable, SSE-friendly                 |
|yt-dlp  |youtube-dl-exec   |Thin Promise/exec wrapper around yt-dlp binary|
|Progress|Server-Sent Events|Unidirectional stream — perfect for download %|
|Runtime |Node.js ESM       |Native async, clean imports                   |

-----

## Install & Run

```bash
npm install
npm run dev     # watch mode
npm start       # production
```

**Requires** `yt-dlp` binary installed on the host system.  
On macOS: `brew install yt-dlp`  
On Linux: `pip install yt-dlp` or the GitHub release binary  
On Windows: `winget install yt-dlp`

**FFmpeg is required** for merging video-only + audio-only streams (1080p+).

-----

## Environment Variables

|Variable     |Default    |Description                       |
|-------------|-----------|----------------------------------|
|PORT         |4000       |Server port                       |
|DOWNLOADS_DIR|./downloads|Where downloaded files are written|
|CORS_ORIGIN  |*          |Restrict CORS in production       |

-----

## API Reference

### `GET /health`

Liveness check.

```json
{ "status": "ok", "service": "yt-dlp-server", "ts": 1718000000000 }
```

-----

### `GET /api/video/info?url=<youtube_url>`

Fetch video metadata and all available formats.

**Query params**

|Param|Required|Description      |
|-----|--------|-----------------|
|url  |✅       |YouTube video URL|

**Example request**

```
GET /api/video/info?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

**Response**

```json
{
  "success": true,
  "data": {
    "id": "dQw4w9WgXcQ",
    "title": "Rick Astley - Never Gonna Give You Up",
    "description": "...",
    "uploader": "Rick Astley",
    "upload_date": "20091025",
    "duration": 213,
    "duration_string": "3:33",
    "view_count": 1400000000,
    "like_count": 14000000,
    "thumbnail": "https://i.ytimg.com/vi/...",
    "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "format_count": 18,
    "formats": [
      {
        "format_id": "137",
        "ext": "mp4",
        "type": "video-only",
        "resolution": "1920x1080",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "filesize": 45234688,
        "filesize_human": "43.1MB",
        "tbr": 1344,
        "vbr": 1344,
        "abr": null,
        "asr": null,
        "vcodec": "avc1.640028",
        "acodec": null,
        "protocol": "https",
        "format_note": null
      },
      {
        "format_id": "140",
        "ext": "m4a",
        "type": "audio-only",
        "resolution": "audio only",
        "width": null,
        "height": null,
        "fps": null,
        "filesize": 4395008,
        "filesize_human": "4.2MB",
        "tbr": 130,
        "vbr": null,
        "abr": 130,
        "asr": 44100,
        "vcodec": null,
        "acodec": "mp4a.40.2",
        "protocol": "https",
        "format_note": null
      }
    ],
    "formats_grouped": {
      "combined": [...],
      "videoOnly": [...],
      "audioOnly": [...]
    }
  }
}
```

-----

### `POST /api/video/download`

Start a download. Responds with an **SSE stream** of progress events.

**Request body (JSON)**

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "quality": "720p",
  "ext": "mp4"
}
```

|Field   |Type  |Required|Options                                                |
|--------|------|--------|-------------------------------------------------------|
|url     |string|✅       |YouTube URL                                            |
|formatId|string|❌       |Specific yt-dlp format_id (e.g. `"137"`)               |
|quality |string|❌       |`"best"` `"1080p"` `"720p"` `"480p"` `"360p"` `"audio"`|
|ext     |string|❌       |`"mp4"` `"webm"` `"m4a"`                               |

`formatId` takes precedence over `quality` if both are provided.

**SSE Event stream**

```
event: progress
data: {"percent":34.5,"size":"23.45MiB","speed":"2.30MiB/s","eta":"00:08"}

event: progress
data: {"percent":68.2,"size":"23.45MiB","speed":"2.15MiB/s","eta":"00:04"}

event: done
data: {"filePath":"/abs/path/to/downloads/Title [id].mp4","format_selector":"bv*[height<=720]+ba/b[height<=720]","url":"..."}

— or on failure —

event: error
data: {"message":"ERROR: [youtube] dQw4w9WgXcQ: Video unavailable"}
```

-----

## Using from Next.js

Since SSE is unidirectional and only works with GET natively in `EventSource`,  
use `fetch` with a `ReadableStream` reader for POST:

```typescript
// lib/yt-api.ts

export type ProgressEvent = {
  percent: number
  size: string
  speed: string
  eta: string
}

export type DoneEvent = {
  filePath: string
  format_selector: string
  url: string
}

const YT_SERVER = process.env.NEXT_PUBLIC_YT_SERVER ?? 'http://localhost:4000'

export async function fetchVideoInfo(url: string) {
  const res = await fetch(
    `${YT_SERVER}/api/video/info?url=${encodeURIComponent(url)}`
  )
  const json = await res.json()
  if (!json.success) throw new Error(json.error)
  return json.data
}

export async function downloadVideo(
  url: string,
  opts: { quality?: string; formatId?: string; ext?: string },
  onProgress: (p: ProgressEvent) => void,
  onDone: (d: DoneEvent) => void,
  onError: (msg: string) => void
) {
  const res = await fetch(`${YT_SERVER}/api/video/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, ...opts }),
  })

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    let currentEvent = ''
    for (const line of lines) {
      if (line.startsWith('event:')) {
        currentEvent = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        const data = JSON.parse(line.slice(5).trim())
        if (currentEvent === 'progress') onProgress(data)
        else if (currentEvent === 'done') onDone(data)
        else if (currentEvent === 'error') onError(data.message)
      }
    }
  }
}
```

-----

## Format Selector Logic

|Quality |Resolved yt-dlp selector                        |
|--------|------------------------------------------------|
|`best`  |`bv*+ba/b`                                      |
|`1080p` |`bv*[height<=1080]+ba/b[height<=1080]`          |
|`720p`  |`bv*[height<=720]+ba/b[height<=720]`            |
|`480p`  |`bv*[height<=480]+ba/b[height<=480]`            |
|`360p`  |`bv*[height<=360]+ba/b[height<=360]`            |
|`audio` |`ba/b` (audio-only stream, no video)            |
|formatId|Raw ID passed directly (e.g. `137`, `140`, `22`)|

For `best`/`1080p`: yt-dlp will download video-only + audio-only streams and  
merge them via FFmpeg into a single container. FFmpeg must be installed.

-----

## Project Structure

```
yt-server/
├── src/
│   ├── index.js              # Express app boot
│   ├── ytdlp.service.js      # Core yt-dlp logic (info + download)
│   └── routes/
│       └── video.js          # /api/video/* route handlers
├── downloads/                # Output dir (auto-created)
├── package.json
└── README.md
```
