import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  esbuild: false,
  build: { minify: false, target: 'esnext' },
  base: '/chat/',
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.DESIGN_RAG_CHAT_API_PROXY || 'http://127.0.0.1:8030',
        changeOrigin: true,
      },
    },
  },
})
