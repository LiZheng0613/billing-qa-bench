/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        canvas: '#f5f5f7',
        line: {
          DEFAULT: '#e8e8ed',
          soft: '#f0f0f4',
          strong: '#dcdfe6',
        },
        ink: {
          DEFAULT: '#111827',
          soft: '#374151',
          muted: '#6b7280',
          faint: '#9ca3af',
        },
        brand: {
          DEFAULT: '#4f46e5',
          deep: '#4338ca',
          soft: '#eef2ff',
          edge: '#e0e7ff',
        },
      },
      fontFamily: {
        sans: ['Inter', 'PingFang SC', 'Microsoft YaHei', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Cascadia Code', 'Consolas', 'monospace'],
      },
      borderRadius: {
        tile: '20px',
      },
      boxShadow: {
        tile: '0 1px 2px rgba(17, 24, 39, 0.04), 0 18px 40px -36px rgba(17, 24, 39, 0.55)',
        'tile-lift': '0 2px 6px rgba(17, 24, 39, 0.06), 0 26px 50px -34px rgba(17, 24, 39, 0.45)',
        rail: '0 1px 2px rgba(17, 24, 39, 0.04), 0 24px 48px -34px rgba(17, 24, 39, 0.4)',
        panel: '0 1px 2px rgba(17, 24, 39, 0.04), 0 18px 40px -36px rgba(17, 24, 39, 0.55)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.25s ease-out',
      },
    },
  },
  plugins: [],
}
