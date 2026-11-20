import '@mantine/core/styles.css'
import './theme/tokens.css'

import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { AuthProvider } from './auth/AuthContext'
import { theme } from './theme/theme'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MantineProvider theme={theme} forceColorScheme="light">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
)
