import { lazy, Suspense, useEffect, type ReactNode } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { ThemeProvider, useTheme } from './context/ThemeContext'
import { AuthProvider, useAuth } from './context/AuthContext'
import TopNav from './components/TopNav'
import GSAPPageTransition from './components/GSAPPageTransition'
import './index.css'

const LoginPage = lazy(() => import('./pages/Login'))
const HomePage = lazy(() => import('./pages/Home'))
const AboutPage = lazy(() => import('./pages/About'))
const DashboardPage = lazy(() => import('./pages/Dashboard'))
const ChatPage = lazy(() => import('./pages/Chat'))
const KnowledgePage = lazy(() => import('./pages/Knowledge'))
const PerformancePage = lazy(() => import('./pages/Performance'))

function RouteLoader() {
  return <div className="ld-route-loader">Đang tải...</div>
}

/* Apply dark/light class to <body> */
function ThemeApplier() {
  const { theme } = useTheme()
  useEffect(() => {
    document.body.classList.toggle('dark', theme === 'dark')
    document.body.classList.toggle('light', theme !== 'dark')
  }, [theme])
  return null
}

/* AppLayout: TopNav + animated page wrapper — only for authenticated pages */
function AppLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <TopNav />
      <GSAPPageTransition>
        {children}
      </GSAPPageTransition>
    </>
  )
}

/* ProtectedRoute: redirect to /login if not authenticated, /dashboard if missing admin */
function ProtectedRoute({
  children,
  requireAdmin = false,
}: {
  children: ReactNode
  requireAdmin?: boolean
}) {
  const { isAuthenticated, isAdmin } = useAuth()
  const location = useLocation()

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  if (requireAdmin && !isAdmin) {
    return <Navigate to="/dashboard" state={{ error: "Bạn cần tài khoản Admin để truy cập mục Tri thức!" }} replace />
  }

  return <AppLayout>{children}</AppLayout>
}

function App() {
  return (
    <AuthProvider>
      <ThemeProvider>
        <ThemeApplier />
        <Router>
          <Suspense fallback={<RouteLoader />}>
            <Routes>
              <Route path="/login"       element={<LoginPage />} />
              <Route path="/"            element={<Navigate to="/home" replace />} />
              <Route path="/home"        element={<AppLayout><HomePage /></AppLayout>} />
              <Route path="/about"       element={<AppLayout><AboutPage /></AppLayout>} />
              <Route path="/dashboard"   element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
              <Route path="/chat"        element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
              <Route path="/knowledge"   element={<ProtectedRoute requireAdmin><KnowledgePage /></ProtectedRoute>} />
              <Route path="/performance" element={<ProtectedRoute><PerformancePage /></ProtectedRoute>} />
              <Route path="*"            element={<Navigate to="/home" replace />} />
            </Routes>
          </Suspense>
        </Router>
      </ThemeProvider>
    </AuthProvider>
  )
}

export default App
