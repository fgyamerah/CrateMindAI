import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import CrateMind from './pages/CrateMind'
import ErrorBoundary from './components/ErrorBoundary'
import Reconciliation from './pages/Reconciliation'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
          <Route path="issues" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
          <Route path="enrichment" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
          <Route path="audit" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
          <Route path="folders" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
          <Route path="reconciliation" element={<ErrorBoundary><Reconciliation /></ErrorBoundary>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
