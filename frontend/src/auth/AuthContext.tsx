import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, post, restoreSession, setAccessToken } from '../api/client'

export type User = {
  id: string
  email: string
  full_name: string
  roles: string[]
}

type Token = { access_token: string }

type AuthState = {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  hasRole: (...roles: string[]) => boolean
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    restoreSession()
      .then((ok) => (ok ? api<User>('/auth/me').then(setUser) : undefined))
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const t = await post<Token>('/auth/login', { email, password })
    setAccessToken(t.access_token)
    setUser(await api<User>('/auth/me'))
  }, [])

  const logout = useCallback(async () => {
    await post('/auth/logout').catch(() => undefined)
    setAccessToken(null)
    setUser(null)
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      user, loading, login, logout,
      hasRole: (...roles) => !!user && roles.some((r) => user.roles.includes(r)),
    }),
    [user, loading, login, logout],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth(): AuthState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth must be used inside AuthProvider')
  return v
}
