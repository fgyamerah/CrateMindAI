import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import CrateMind from './pages/CrateMind'
import ErrorBoundary from './components/ErrorBoundary'
import Reconciliation from './pages/Reconciliation'
import MetadataRepair from './pages/MetadataRepair'
import MetadataSanitation from './pages/MetadataSanitation'
import Quality from './pages/Quality'

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
          <Route path="quality" element={<ErrorBoundary><Quality /></ErrorBoundary>} />
          <Route path="metadata-repair" element={<ErrorBoundary><MetadataRepair /></ErrorBoundary>} />
          <Route path="metadata-sanitation" element={<ErrorBoundary><MetadataSanitation /></ErrorBoundary>} />
          <Route path="reconciliation" element={<ErrorBoundary><Reconciliation /></ErrorBoundary>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
