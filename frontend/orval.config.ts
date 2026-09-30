import { defineConfig } from 'orval'

export default defineConfig({
  narravant: {
    input: { target: '../backend/openapi.json' },
    output: {
      client: 'zod',
      mode: 'single',
      target: './src/api/generated/contracts.ts',
      override: { zod: { version: 4, variant: 'classic', strict: true } },
    },
  },
})
