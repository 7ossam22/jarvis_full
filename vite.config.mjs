import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'
import { viteStaticCopy } from 'vite-plugin-static-copy'

// WEB_MODE=true → HTTPS on port 5174 for local network access
// default      → HTTP on port 5173 for Electron dev
const webMode = process.env.WEB_MODE === 'true'

export default defineConfig({
  plugins: [
    react(),
    ...(webMode ? [basicSsl()] : []),
    // Copy ONNX WASM files to dist/ root so they're served at /ort-wasm*.wasm
    // Required in production — in dev, node_modules are served directly by Vite
    viteStaticCopy({
      targets: [{ src: 'node_modules/onnxruntime-web/dist/*.wasm', dest: '.' }],
    }),
  ],
  base: './',
  // Transformers.js uses dynamic WASM imports — don't pre-bundle it
  optimizeDeps: {
    exclude: ['@xenova/transformers'],
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,  // ONNX runtime is large by design
  },
  server: {
    host: '0.0.0.0',
    port: webMode ? 5174 : 5173,
    strictPort: true,
    https: webMode || undefined,
    // Allow Vite dev server to serve files from node_modules (for ONNX WASM)
    fs: { allow: ['..'] },
  },
})
