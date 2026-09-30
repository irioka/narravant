import fs from 'node:fs'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const appVersion = fs.readFileSync(path.resolve(import.meta.dirname, '../VERSION'), 'utf8').trim()

// https://vite.dev/config/
export default defineConfig({
  define: {
    __NARRAVANT_VERSION__: JSON.stringify(appVersion),
  },
  build: {
    rolldownOptions: {
      output: {
        manualChunks: (id) => {
          if (id.includes('/node_modules/recharts/')) return 'recharts'
          if (id.includes('/node_modules/lucide-react/')) return 'lucide'
          if (id.includes('/node_modules/radix-ui/')) return 'radix-ui'
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/') || id.includes('/node_modules/react-router')) return 'react'
          return undefined
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
      '/healthz': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
