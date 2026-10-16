import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@sarae/ui': fileURLToPath(new URL('../vendor/react-ui-core/packages/ui/src/index.ts', import.meta.url)),
    },
  },
})
