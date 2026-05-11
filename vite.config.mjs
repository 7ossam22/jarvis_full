import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'

// WEB_MODE=true → HTTPS on port 5174 for local network access
// default      → HTTP on port 5173 for Electron dev
const webMode = process.env.WEB_MODE === 'true'

export default defineConfig({
  plugins: [
    react(),
    ...(webMode ? [basicSsl()] : []),
  ],
  base: './',
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    emptyOutDir: true,
    target: 'esnext',
  },
  server: {
    host: '0.0.0.0',
    port: webMode ? 5174 : 5173,
    strictPort: true,
    https: webMode || undefined,
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp',
    },
  },
  optimizeDeps: {
    exclude: ['@xenova/transformers'],
  },
  worker: {
    format: 'es',
  },
})
