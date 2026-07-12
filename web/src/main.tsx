import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/geist-sans/400.css'
import '@fontsource/geist-sans/500.css'
import '@fontsource/geist-sans/600.css'
import '@fontsource/geist-sans/700.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/geist-mono/500.css'
import './styles/tokens.css'
import './styles/global.css'
import { applyUiTheme, asUiTheme, watchSystemTheme } from './lib/theme'
import { useForge } from './state/store'
import App from './App'

// Apply before first paint to avoid a dark flash in light mode.
applyUiTheme(asUiTheme(localStorage.getItem('forge.uiTheme')))
watchSystemTheme(() => useForge.getState().uiTheme)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
