import { createNarravantI18n } from '@/i18n/i18n'
import '@testing-library/jest-dom/vitest'

createNarravantI18n('en')

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    addEventListener: () => undefined,
    addListener: () => undefined,
    dispatchEvent: () => false,
    matches: false,
    media: query,
    onchange: null,
    removeEventListener: () => undefined,
    removeListener: () => undefined,
  }),
})

class TestResizeObserver {
  observe() { /* Radix Slider only needs the observer to exist in jsdom. */ }
  unobserve() { /* no-op */ }
  disconnect() { /* no-op */ }
}

Object.defineProperty(window, 'ResizeObserver', {
  configurable: true,
  value: TestResizeObserver,
})
