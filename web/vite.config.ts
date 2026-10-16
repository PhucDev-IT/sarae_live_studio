import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: [
      {
        find: /^@sarae\/ui\/styles$/,
        replacement: fileURLToPath(new URL('../vendor/react-ui-core/packages/ui/dist/styles.css', import.meta.url)),
      },
      {
        find: /^@sarae\/ui$/,
        replacement: fileURLToPath(new URL('../vendor/react-ui-core/packages/ui/dist/index.js', import.meta.url)),
      },
    ],
  },
})
