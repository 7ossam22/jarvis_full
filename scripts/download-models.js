#!/usr/bin/env node
// Run once: node scripts/download-models.js
// Downloads Xenova/whisper-tiny.en ONNX files to public/models/ so the app
// never touches the internet at runtime.

const https = require('https')
const http  = require('http')
const fs    = require('fs')
const path  = require('path')

const MODEL_ID = 'Xenova/whisper-tiny.en'
const BASE_URL = `https://huggingface.co/${MODEL_ID}/resolve/main`
const DEST_DIR = path.join(__dirname, '..', 'public', 'models', MODEL_ID)

// Only the files @xenova/transformers actually requests at inference time
const FILES = [
  'config.json',
  'generation_config.json',
  'preprocessor_config.json',
  'special_tokens_map.json',
  'tokenizer.json',
  'tokenizer_config.json',
  'vocab.json',
  'onnx/encoder_model_quantized.onnx',
  'onnx/decoder_model_merged_quantized.onnx',
]

function download(url, dest, hops) {
  hops = hops || 0
  return new Promise((resolve, reject) => {
    if (hops > 10) return reject(new Error('Too many redirects'))
    const lib = url.startsWith('https://') ? https : http
    const req = lib.get(url, { headers: { 'User-Agent': 'transformers.js/2' }, timeout: 300000 }, (res) => {
      if ([301, 302, 307, 308].includes(res.statusCode)) {
        res.resume()
        const loc = (res.headers.location || '').trim()
        const next = loc.startsWith('http') ? loc : new URL(loc, url).href
        return download(next, dest, hops + 1).then(resolve).catch(reject)
      }
      if (res.statusCode !== 200) {
        res.resume()
        return reject(new Error(`HTTP ${res.statusCode} for ${url}`))
      }
      fs.mkdirSync(path.dirname(dest), { recursive: true })
      const out = fs.createWriteStream(dest)
      let downloaded = 0
      const total = parseInt(res.headers['content-length'] || '0', 10)
      res.on('data', chunk => {
        downloaded += chunk.length
        if (total > 0) {
          const pct = Math.round((downloaded / total) * 100)
          process.stdout.write(`\r    ${pct}% (${Math.round(downloaded / 1024 / 1024)}MB)   `)
        }
      })
      res.pipe(out)
      out.on('finish', () => { out.close(); process.stdout.write('\n'); resolve() })
      out.on('error', reject)
    })
    req.on('error', reject)
    req.on('timeout', () => { req.destroy(); reject(new Error('Timed out')) })
  })
}

async function main() {
  console.log(`\nDownloading ${MODEL_ID} for offline use...\n`)
  for (const file of FILES) {
    const dest = path.join(DEST_DIR, file)
    if (fs.existsSync(dest)) {
      console.log(`  ✓ ${file}  (already downloaded)`)
      continue
    }
    process.stdout.write(`  ↓ ${file}\n`)
    try {
      await download(`${BASE_URL}/${file}`, dest)
    } catch (err) {
      console.error(`\n  ✗ Failed to download ${file}: ${err.message}`)
      process.exit(1)
    }
  }
  console.log('\n✓ All model files ready. You can now run: npm run dev\n')
}

main()
