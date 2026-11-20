import { MantineProvider } from '@mantine/core'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Login from './Login'

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ user: null, login: vi.fn() }),
}))

// Mantine needs matchMedia, which jsdom lacks.
window.matchMedia ??= ((q: string) => ({
  matches: false, media: q, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia

describe('Login page', () => {
  it('shows the sign-in form and the audit notice', () => {
    render(<MantineProvider><MemoryRouter><Login /></MemoryRouter></MantineProvider>)
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByText(/Activity is logged/)).toBeInTheDocument()
    expect(screen.queryByText(/two-factor|verification code|authenticator/i)).not.toBeInTheDocument()  // password only
  })
})
