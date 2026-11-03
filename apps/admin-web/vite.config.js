import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // The production SPA is mounted by FastAPI at /admin/.
  // Avoid Vite's final esbuild transpile, which is unreliable in this local Windows Node runtime.
  esbuild: false,
  build: { minify: false, target: 'esnext' },
  base: '/admin/',
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.DESIGN_RAG_ADMIN_API_PROXY || 'http://127.0.0.1:8030',
        changeOrigin: true,
      },
    },
  },
})