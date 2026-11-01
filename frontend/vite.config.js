
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    host: 'localhost',
    port: 5173,
    proxy: {
      '/health': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/datasets': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/documents': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/graph': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/relationships': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/conversations': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
