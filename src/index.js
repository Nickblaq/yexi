
/**
 * index.js — yt-dlp server entry point
 *
 * A clean, purpose-built HTTP server for YouTube video
 * metadata and download operations via yt-dlp.
 *
 * Designed to be called from a Next.js app (or any HTTP client).
 * Exposes two core capabilities:
 *   1. /api/video/info     — get formats + metadata
 *   2. /api/video/download — download with SSE progress stream
 */

import express from 'express'
import videoRoutes from './routes/video.js'

const app = express()
const PORT = process.env.PORT ?? 4000

// -------------------------------------------------------------------
// Middleware
// -------------------------------------------------------------------

app.use(express.json())

// Basic CORS — tighten this for production
app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', process.env.CORS_ORIGIN ?? '*')
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type')
  if (req.method === 'OPTIONS') return res.sendStatus(204)
  next()
})

// Request logger
app.use((req, _res, next) => {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.path}`)
  next()
})

// -------------------------------------------------------------------
// Routes
// -------------------------------------------------------------------

app.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'yt-dlp-server', ts: Date.now() })
})

app.use('/api/video', videoRoutes)

// 404 fallback
app.use((_req, res) => {
  res.status(404).json({ success: false, error: 'Route not found' })
})

// -------------------------------------------------------------------
// Boot
// -------------------------------------------------------------------

app.listen(PORT, () => {
  console.log(`
╔══════════════════════════════════════════╗
║         yt-dlp server running            ║
╠══════════════════════════════════════════╣
║  Port     : ${PORT}                          ║
║  Health   : GET  /health                 ║
║  Info     : GET  /api/video/info?url=    ║
║  Download : POST /api/video/download     ║
╚══════════════════════════════════════════╝
  `)
})

export default app
