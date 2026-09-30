import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { createLocaleRuntime, LocaleProvider } from './i18n/LocaleProvider'
import './index.css'

const localeRuntime = createLocaleRuntime()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LocaleProvider runtime={localeRuntime}>
      <App />
    </LocaleProvider>
  </StrictMode>,
)
