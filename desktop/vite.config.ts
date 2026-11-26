import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Tauri expects a fixed port and must not watch Rust/Python build output: on Windows the
// watcher otherwise hits EBUSY on locked files under src-tauri/target and binaries/.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  clearScreen: false,
  server: {
    host: '127.0.0.1',
    port: 1420,
    strictPort: true,
    proxy: {
      '/studio': {
        target: loadEnv(mode, '.', 'SARAE_').SARAE_STUDIO_URL || 'http://127.0.0.1:8793',
        changeOrigin: true,
        // SSE and large uploads must remain streamed.
        timeout: 0,
        proxyTimeout: 0,
      },
    },
    watch: {
      ignored: [
        '**/src-tauri/target/**',
        '**/src-tauri/binaries/**',
        '**/src-tauri/gen/**',
        '**/dist/**',
        '**/node_modules/**',
        '**/.venv/**',
      ],
    },
  },
  envPrefix: ['VITE_', 'TAURI_ENV_'],
  build: {
    target: 'chrome110',
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
}))
