/**
 * 演示专用 vite 配置：代理指向本地 mock 后端（8001），
 * 与真实后端（8000）/常规 dev server（5173）互不影响。
 * 用法：npx vite --config vite.mock.config.ts
 * 临时演示资产，不入库。
 */
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8877',
        changeOrigin: true,
        ws: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8877',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts'],
          vue: ['vue', 'vue-router', 'pinia'],
        },
      },
    },
  },
})
