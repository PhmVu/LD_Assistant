import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // Split large vendor chunks to avoid truncation
    chunkSizeWarningLimit: 3000,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          // Split plotly into its own chunk (very large)
          if (id.includes('plotly') || id.includes('d3-')) {
            return 'vendor-plotly'
          }
          // Split GSAP into own chunk
          if (id.includes('gsap')) {
            return 'vendor-gsap'
          }
          // Split react ecosystem
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom') || id.includes('react-router')) {
            return 'vendor-react'
          }
          // Everything else from node_modules
          if (id.includes('node_modules')) {
            return 'vendor'
          }
        },
      },
    },
  },
  server: {
    port: 7789,
    proxy: {
      // Proxy all /api/* requests to FastAPI backend during dev
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        secure: false,
      },
    },
  },
})
