
/**
 * routes/video.js
 *
 * REST routes for:
 *   GET  /api/video/info     - fetch metadata + all formats for a URL
 *   POST /api/video/download - start a download, stream progress via SSE
 *
 * SSE (Server-Sent Events) is used for the download endpoint so the Next.js
 * client can receive live progress without polling. The final done/error
 * event closes the stream.
 */

import { Router } from 'express'
import { getVideoInfo, downloadVideo } from '../ytdlp.service.js'

const router = Router()

// -------------------------------------------------------------------
// Validation
// -------------------------------------------------------------------

function isValidYouTubeUrl(url) {
  try {
    const u = new URL(url)
    return (
      (u.hostname === 'www.youtube.com' ||
        u.hostname === 'youtube.com' ||
        u.hostname === 'youtu.be' ||
        u.hostname === 'm.youtube.com') &&
      (u.pathname.startsWith('/watch') ||
        u.pathname.startsWith('/shorts') ||
        u.hostname === 'youtu.be')
    )
  } catch {
    return false
  }
}

// -------------------------------------------------------------------
// GET /api/video/info?url=<youtube_url>
// -------------------------------------------------------------------

/**
 * Returns video metadata and all classified available formats.
 *
 * Response shape:
 * {
 *   success: true,
 *   data: {
 *     id, title, description, uploader, duration, view_count,
 *     thumbnail, webpage_url,
 *     format_count,
 *     formats: [...],       // flat list
 *     formats_grouped: {    // same data, bucketed
 *       combined, videoOnly, audioOnly
 *     }
 *   }
 * }
 */
router.get('/info', async (req, res) => {
  const { url } = req.query

  if (!url) {
    return res.status(400).json({
      success: false,
      error: 'Missing required query param: url',
    })
  }

  if (!isValidYouTubeUrl(url)) {
    return res.status(400).json({
      success: false,
      error: 'Invalid or unsupported URL. Only YouTube URLs are accepted.',
    })
  }

  try {
    const info = await getVideoInfo(url)
    res.json({ success: true, data: info })
  } catch (err) {
    console.error('[/info] yt-dlp error:', err.message)
    res.status(500).json({
      success: false,
      error: err.message ?? 'Failed to fetch video info',
    })
  }
})

// -------------------------------------------------------------------
// POST /api/video/download
// -------------------------------------------------------------------

/**
 * Starts a download and streams progress via Server-Sent Events.
 *
 * Request body (JSON):
 * {
 *   url:      string,             // required — YouTube URL
 *   formatId: string,             // optional — specific yt-dlp format_id
 *   quality:  "best" | "1080p" | "720p" | "480p" | "360p" | "audio",
 *   ext:      "mp4" | "webm" | "m4a",
 * }
 *
 * SSE Event stream:
 *   event: progress   data: { percent, size, speed, eta }
 *   event: done       data: { filePath, format_selector, url }
 *   event: error      data: { message }
 *
 * The stream closes automatically after 'done' or 'error'.
 *
 * Client usage example (Next.js):
 *   const es = new EventSource('/api/video/download') — for POST you'd use
 *   fetch() with a ReadableStream reader instead. See README.
 */
router.post('/download', async (req, res) => {
  const { url, formatId, quality = 'best', ext } = req.body ?? {}

  if (!url) {
    return res.status(400).json({ success: false, error: 'Missing required field: url' })
  }

  if (!isValidYouTubeUrl(url)) {
    return res.status(400).json({
      success: false,
      error: 'Invalid or unsupported URL. Only YouTube URLs are accepted.',
    })
  }

  // Set up SSE headers
  res.setHeader('Content-Type', 'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection', 'keep-alive')
  res.setHeader('X-Accel-Buffering', 'no') // disable nginx buffering if present
  res.flushHeaders()

  const sendEvent = (event, data) => {
    res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
  }

  const { emitter, promise } = downloadVideo(url, { formatId, quality, ext })

  emitter.on('progress', (progress) => {
    sendEvent('progress', progress)
  })

  emitter.on('done', (result) => {
    sendEvent('done', result)
    res.end()
  })

  emitter.on('error', (err) => {
    sendEvent('error', { message: err.message })
    res.end()
  })

  // Catch unhandled rejection from the promise too
  promise.catch((err) => {
    if (!res.writableEnded) {
      sendEvent('error', { message: err.message })
      res.end()
    }
  })

  // Clean up if client disconnects mid-download
  req.on('close', () => {
    emitter.removeAllListeners()
  })
})

export default router
