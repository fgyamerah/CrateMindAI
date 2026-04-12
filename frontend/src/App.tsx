import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Jobs from './pages/Jobs'
import Tracks from './pages/Tracks'
import BpmReview from './pages/BpmReview'
import SetBuilder from './pages/SetBuilder'
import Export from './pages/Export'
import SsdSync from './pages/SsdSync'
import Settings from './pages/Settings'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="jobs" element={<Jobs />} />
          <Route path="tracks" element={<Tracks />} />
          <Route path="bpm-review" element={<BpmReview />} />
          <Route path="set-builder" element={<SetBuilder />} />
          <Route path="export" element={<Export />} />
          <Route path="ssd-sync" element={<SsdSync />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
