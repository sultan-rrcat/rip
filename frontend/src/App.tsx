import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import Home from '@/pages/Home'
import Notebook from '@/pages/Notebook'
import Login from '@/pages/Login'
import Admin from '@/pages/Admin'
import ErrorBoundary from '@/components/ErrorBoundary'
import { isAdminRole } from '@/auth'
import type { AuthUser } from '@/auth'

export default function App() {
  const [auth, setAuth] = useState<AuthUser | null | undefined>(undefined)

  useEffect(() => {
    let cancelled = false
    fetch('/api/auth/me', { credentials: 'include' })
      .then(async (res) => {
        if (cancelled) return
        if (!res.ok) {
          setAuth(null)
          return
        }
        const data = await res.json().catch(() => ({}))
        if (!cancelled) setAuth({ username: data.username ?? '', role: data.role ?? 'viewer' })
      })
      .catch(() => {
        if (!cancelled) setAuth(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (auth === undefined) {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-100">
        <p>Loading...</p>
      </div>
    )
  }

  const isAuthenticated = auth !== null
  const isAdmin = isAdminRole(auth?.role)

  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/login"
          element={isAuthenticated ? <Navigate to="/" replace /> : <Login onLogin={(user) => setAuth(user)} />}
        />
        <Route
          path="/"
          element={isAuthenticated ? <Home isAdmin={isAdmin} /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/notebook/:notebook_id"
          element={
            isAuthenticated ? (
              <ErrorBoundary>
                <Notebook />
              </ErrorBoundary>
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
        <Route
          path="/admin"
          element={
            !isAuthenticated ? (
              <Navigate to="/login" replace />
            ) : isAdmin ? (
              <Admin />
            ) : (
              <Navigate to="/" replace />
            )
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
