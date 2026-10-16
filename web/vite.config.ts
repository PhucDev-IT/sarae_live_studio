import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import path from 'node:path'

const webNodeModules = fileURLToPath(new URL('./node_modules', import.meta.url))
const uiSource = fileURLToPath(new URL('../vendor/react-ui-core/packages/ui/src', import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // The git submodule is outside web/, so dependencies must resolve from web/node_modules.
    dedupe: ['react', 'react-dom'],
    alias: [
      { find: /^@sarae\/ui\/styles$/, replacement: path.join(uiSource, 'theme/index.scss') },
      { find: /^@sarae\/ui$/, replacement: path.join(uiSource, 'index.ts') },
      { find: /^react$/, replacement: path.join(webNodeModules, 'react') },
      { find: /^react\/(jsx-runtime|jsx-dev-runtime)$/, replacement: path.join(webNodeModules, 'react/$1') },
      { find: /^react-dom$/, replacement: path.join(webNodeModules, 'react-dom') },
      { find: /^react-dom\/(.*)$/, replacement: path.join(webNodeModules, 'react-dom/$1') },
    ],
  },
})
