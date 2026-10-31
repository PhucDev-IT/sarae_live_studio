import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/**
 * The OBS overlay renderer page (overlay.html → src/overlay/obs), built into the Windows Agent so the Browser Source
 * loads it from 127.0.0.1 with no Internet access. Same OverlayRenderer code as the web editor preview.
 * `npm run build:overlay` regenerates agent-core/src/sarae_agent/overlay/renderer (committed, shipped by PyInstaller).
 */
export default defineConfig({
  plugins: [react()],
  base: './',
  publicDir: false,
  build: {
    outDir: fileURLToPath(new URL('../agent-core/src/sarae_agent/overlay/renderer', import.meta.url)),
    emptyOutDir: true,
    assetsInlineLimit: 0,
    rollupOptions: {input: fileURLToPath(new URL('./overlay.html', import.meta.url))},
  },
})
