import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  server: {
    host: '0.0.0.0',
    port: 5178,
    proxy: {
      // Same-origin dev access: both API families reach the backend at
      // http://localhost:8000 (uvicorn app.main:app from rip/backend/).
      '/api/': 'http://localhost:8000',
      '/v1/': 'http://localhost:8000',
    },
  },
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
})
