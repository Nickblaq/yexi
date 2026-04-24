
/**
 * test.js — smoke test for getVideoInfo
 *
 * Calls the service directly (no HTTP layer involved).
 * Run: node test.js
 */

import { getVideoInfo } from './src/ytdlp.service.js'

const VIDEO_URL = 'https://m.youtube.com/watch?v=asBeyqk_zpk&t=8s'

console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
console.log('  yt-dlp server — getVideoInfo smoke test')
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
console.log(`  URL: ${VIDEO_URL}`)
console.log('  Fetching...\n')

try {
  const info = await getVideoInfo(VIDEO_URL)

  // ── Core metadata ──────────────────────────────────────
  console.log('✅ SUCCESS\n')
  console.log('── Metadata ──────────────────────────────')
  console.log(`  id             : ${info.id}`)
  console.log(`  title          : ${info.title}`)
  console.log(`  uploader       : ${info.uploader}`)
  console.log(`  duration       : ${info.duration_string} (${info.duration}s)`)
  console.log(`  upload_date    : ${info.upload_date}`)
  console.log(`  view_count     : ${info.view_count?.toLocaleString()}`)
  console.log(`  like_count     : ${info.like_count?.toLocaleString()}`)
  console.log(`  thumbnail      : ${info.thumbnail}`)
  console.log(`  webpage_url    : ${info.webpage_url}`)
  console.log(`  description    : ${info.description?.slice(0, 120)}...`)

  // ── Format summary ─────────────────────────────────────
  console.log('\n── Formats ───────────────────────────────')
  console.log(`  total          : ${info.format_count}`)
  console.log(`  video+audio    : ${info.formats_grouped.combined.length}`)
  console.log(`  video-only     : ${info.formats_grouped.videoOnly.length}`)
  console.log(`  audio-only     : ${info.formats_grouped.audioOnly.length}`)

  // ── Best picks per category ────────────────────────────
  const bestCombined = info.formats_grouped.combined.at(-1)
  const bestVideo    = info.formats_grouped.videoOnly.at(-1)
  const bestAudio    = info.formats_grouped.audioOnly.at(-1)

  console.log('\n── Best formats (last = highest quality) ─')
  if (bestCombined) {
    console.log(`  combined  → [${bestCombined.format_id}] ${bestCombined.ext} ${bestCombined.resolution} | ${bestCombined.filesize_human}`)
  }
  if (bestVideo) {
    console.log(`  video     → [${bestVideo.format_id}] ${bestVideo.ext} ${bestVideo.resolution} ${bestVideo.fps}fps | ${bestVideo.filesize_human}`)
  }
  if (bestAudio) {
    console.log(`  audio     → [${bestAudio.format_id}] ${bestAudio.ext} ${bestAudio.abr}kbps | ${bestAudio.filesize_human}`)
  }

  // ── Full raw dump ──────────────────────────────────────
  console.log('\n── Full response (JSON) ──────────────────')
  console.log(JSON.stringify(info, null, 2))

} catch (err) {
  console.error('❌ FAILED\n')
  console.error(err.message)
  process.exit(1)
}
