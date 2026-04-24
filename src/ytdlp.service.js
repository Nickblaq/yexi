
/**
 * ytdlp.service.js
 *
 * Core abstraction over youtube-dl-exec (yt-dlp binary).
 * Handles: metadata fetch, format listing, and download orchestration.
 *
 * Design notes:
 *  - Uses dumpSingleJson to get all metadata + formats in a single subprocess call.
 *  - Format classification is done at the service layer so routes stay thin.
 *  - Downloads write to a configurable output dir and stream progress via EventEmitter.
 *  - noCheckCertificates is set globally because yt-dlp occasionally errors on
 *    certificate chain issues in server environments.
 */

import youtubedl from 'youtube-dl-exec'
import { createWriteStream, mkdirSync, existsSync } from 'fs'
import { join, resolve } from 'path'
import { EventEmitter } from 'events'

// -------------------------------------------------------------------
// Config
// -------------------------------------------------------------------

const DOWNLOADS_DIR = resolve(process.env.DOWNLOADS_DIR ?? './downloads')
const BASE_YT_FLAGS = {
  noCheckCertificates: true,
  noWarnings: true,
  addHeader: [
    'referer:youtube.com',
    'user-agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
  ],
}

// Ensure download directory exists on module load
if (!existsSync(DOWNLOADS_DIR)) {
  mkdirSync(DOWNLOADS_DIR, { recursive: true })
}

// -------------------------------------------------------------------
// Format helpers
// -------------------------------------------------------------------

/**
 * Classifies a raw yt-dlp format object into a clean, API-friendly shape.
 * Filters out storyboard/mhtml entries which are thumbnails, not playable video.
 */
function classifyFormat(fmt) {
  const hasVideo = fmt.vcodec && fmt.vcodec !== 'none'
  const hasAudio = fmt.acodec && fmt.acodec !== 'none'

  let type
  if (hasVideo && hasAudio) type = 'video+audio'
  else if (hasVideo) type = 'video-only'
  else if (hasAudio) type = 'audio-only'
  else return null // storyboards, mhtml thumbnails — skip

  return {
    format_id: fmt.format_id,
    ext: fmt.ext,
    type,
    resolution: fmt.resolution ?? (hasAudio && !hasVideo ? 'audio only' : null),
    width: fmt.width ?? null,
    height: fmt.height ?? null,
    fps: fmt.fps ?? null,
    filesize: fmt.filesize ?? fmt.filesize_approx ?? null,
    filesize_human: fmt.filesize
      ? humanBytes(fmt.filesize)
      : fmt.filesize_approx
        ? `~${humanBytes(fmt.filesize_approx)}`
        : 'unknown',
    tbr: fmt.tbr ?? null,       // total bitrate kbps
    vbr: fmt.vbr ?? null,       // video bitrate kbps
    abr: fmt.abr ?? null,       // audio bitrate kbps
    asr: fmt.asr ?? null,       // audio sample rate hz
    vcodec: hasVideo ? fmt.vcodec : null,
    acodec: hasAudio ? fmt.acodec : null,
    protocol: fmt.protocol ?? null,
    format_note: fmt.format_note ?? null,
  }
}

function humanBytes(bytes) {
  if (!bytes) return 'unknown'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(1)}${units[i]}`
}

// -------------------------------------------------------------------
// Service functions
// -------------------------------------------------------------------

/**
 * Fetches video metadata and all available formats.
 * Returns a structured response ready for the API.
 *
 * @param {string} url - YouTube video URL
 * @returns {Promise<VideoInfo>}
 */
export async function getVideoInfo(url) {
  const raw = await youtubedl(url, {
    ...BASE_YT_FLAGS,
    dumpSingleJson: true,
    preferFreeFormats: true,
  })

  const formats = (raw.formats ?? [])
    .map(classifyFormat)
    .filter(Boolean)

  // Group formats for convenience
  const grouped = {
    combined: formats.filter(f => f.type === 'video+audio'),
    videoOnly: formats.filter(f => f.type === 'video-only'),
    audioOnly: formats.filter(f => f.type === 'audio-only'),
  }

  return {
    id: raw.id,
    title: raw.title,
    description: raw.description?.slice(0, 300) ?? null,
    uploader: raw.uploader ?? null,
    upload_date: raw.upload_date ?? null,   // YYYYMMDD
    duration: raw.duration ?? null,          // seconds
    duration_string: raw.duration_string ?? null,
    view_count: raw.view_count ?? null,
    like_count: raw.like_count ?? null,
    thumbnail: raw.thumbnail ?? null,
    webpage_url: raw.webpage_url ?? url,
    formats,
    formats_grouped: grouped,
    format_count: formats.length,
  }
}

/**
 * Downloads a video using a specific format selector or yt-dlp format string.
 * Returns a DownloadResult with the final file path.
 *
 * Emits progress events via the returned EventEmitter.
 * Events: 'progress' { percent, size, speed, eta }
 *         'done'     { filePath, format_id }
 *         'error'    Error
 *
 * @param {string} url
 * @param {object} opts
 * @param {string} [opts.formatId]   - specific format_id (e.g. "137")
 * @param {string} [opts.quality]    - preset: "best" | "1080p" | "720p" | "480p" | "360p" | "audio"
 * @param {string} [opts.ext]        - preferred extension: "mp4" | "webm" | "m4a" | "mp3"
 * @returns {{ emitter: EventEmitter, promise: Promise<DownloadResult> }}
 */
export function downloadVideo(url, opts = {}) {
  const emitter = new EventEmitter()

  const formatSelector = resolveFormatSelector(opts)
  const outputTemplate = join(DOWNLOADS_DIR, '%(title)s [%(id)s].%(ext)s')

  const promise = new Promise((resolve, reject) => {
    const subprocess = youtubedl.exec(url, {
      ...BASE_YT_FLAGS,
      format: formatSelector,
      output: outputTemplate,
      // Merge video+audio streams when downloading separate streams
      mergeOutputFormat: opts.ext === 'mp4' ? 'mp4' : 'mkv',
      // Print progress to stdout so we can parse it
      progress: true,
      newline: true,
    })

    let finalFilePath = null

    subprocess.stdout?.on('data', (chunk) => {
      const line = chunk.toString().trim()

      // Parse download progress line: [download]  45.3% of   34.56MiB at    2.34MiB/s ETA 00:12
      const progressMatch = line.match(
        /\[download\]\s+([\d.]+)%\s+of\s+([\S]+)\s+at\s+([\S]+)\s+ETA\s+([\S]+)/
      )
      if (progressMatch) {
        emitter.emit('progress', {
          percent: parseFloat(progressMatch[1]),
          size: progressMatch[2],
          speed: progressMatch[3],
          eta: progressMatch[4],
        })
        return
      }

      // Parse destination line to get actual output file path
      const destMatch = line.match(/\[(?:download|Merger)\] Destination:\s+(.+)/)
      if (destMatch) {
        finalFilePath = destMatch[1].trim()
      }

      // Detect merged output file
      const mergeMatch = line.match(/\[Merger\] Merging formats into "(.+)"/)
      if (mergeMatch) {
        finalFilePath = mergeMatch[1].trim()
      }
    })

    subprocess.stderr?.on('data', (chunk) => {
      const line = chunk.toString().trim()
      if (line && !line.startsWith('WARNING')) {
        // Only emit genuine errors, not warnings
        if (line.startsWith('ERROR')) {
          emitter.emit('error', new Error(line))
        }
      }
    })

    subprocess
      .then(() => {
        const result = {
          filePath: finalFilePath,
          downloadsDir: DOWNLOADS_DIR,
          format_selector: formatSelector,
          url,
        }
        emitter.emit('done', result)
        resolve(result)
      })
      .catch((err) => {
        emitter.emit('error', err)
        reject(err)
      })
  })

  return { emitter, promise }
}

/**
 * Resolves user-facing quality/format options into a yt-dlp format selector string.
 */
function resolveFormatSelector({ formatId, quality, ext }) {
  // Explicit format ID takes priority
  if (formatId) return formatId

  // Quality presets
  const qualityMap = {
    best:    'bv*+ba/b',          // best video + best audio, merged
    '1080p': 'bv*[height<=1080]+ba/b[height<=1080]',
    '720p':  'bv*[height<=720]+ba/b[height<=720]',
    '480p':  'bv*[height<=480]+ba/b[height<=480]',
    '360p':  'bv*[height<=360]+ba/b[height<=360]',
    audio:   'ba/b',              // best audio-only
  }

  if (quality && qualityMap[quality]) {
    let selector = qualityMap[quality]
    // If ext preference given, prepend ext filter
    if (ext && quality !== 'audio') {
      selector = `bv*[height<=${quality.replace('p','')}][ext=${ext}]+ba/` + selector
    }
    return selector
  }

  // Default: best quality with video+audio
  return 'bv*+ba/b'
}
