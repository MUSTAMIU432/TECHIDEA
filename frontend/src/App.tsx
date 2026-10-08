import { RouterProvider } from 'react-router-dom'

import { router } from './app/routes'
import { ErrorBoundary } from './components/ErrorBoundary'
import { AuthProvider } from './features/identity/auth/AuthContext'

function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </ErrorBoundary>
  )
}

export default App
