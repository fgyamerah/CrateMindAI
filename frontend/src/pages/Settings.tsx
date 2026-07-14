import { Fragment } from 'react'

/**
 * Settings page — placeholder for backend config inspection.
 * Future: expose config.py tunables (paths, flags, thresholds) via /api/config.
 */

interface SettingItem {
  label: string
  value: string
}

interface SettingGroup {
  title: string
  items: SettingItem[]
}

const SETTINGS_GROUPS: SettingGroup[] = [
  {
    title: 'Pipeline',
    items: [
      { label: 'MIK-first mode', value: 'Enabled (BPM/key/cue writes skipped when value exists)' },
      { label: 'Cue suggest', value: 'Disabled by default (--force-cue-suggest to enable)' },
      { label: 'Rekordbox XML export', value: 'Disabled by default (--force-xml to enable)' },
      { label: 'ID3 version', value: 'ID3v2.3 (Rekordbox compatible)' },
    ],
  },
  {
    title: 'Paths (read from backend config)',
    items: [
      { label: 'Music root', value: 'Configure via DJ_MUSIC_ROOT env var or config_local.py' },
      { label: 'Rekordbox Linux root', value: 'Configure via RB_LINUX_ROOT env var' },
      { label: 'Windows drive letter', value: 'Configure via RB_WIN_DRIVE env var (default: E)' },
    ],
  },
  {
    title: 'External binaries',
    items: [
      { label: 'ffprobe', value: 'Required for QC and convert-audio' },
      { label: 'rmlint', value: 'Required for dedupe' },
      { label: 'aubio / aubiobpm', value: 'BPM analysis (librosa fallback)' },
      { label: 'keyfinder-cli', value: 'Key detection (Camelot mapping)' },
      { label: 'beet', value: 'MusicBrainz metadata lookup (--skip-beets to bypass)' },
    ],
  },
]

export default function Settings() {
  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Settings</h1>
      </div>

      <p className="muted section">
        Live config editing is not yet implemented. Settings are managed via{' '}
        <code>config.py</code> / <code>config_local.py</code> and environment variables.
        This page reflects the current design constraints.
      </p>

      {SETTINGS_GROUPS.map((group) => (
        <section className="section" key={group.title}>
          <div className="card">
            <h2 className="card-title">{group.title}</h2>
            <dl className="def-list">
              {group.items.map(({ label, value }) => (
                <Fragment key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </Fragment>
              ))}
            </dl>
          </div>
        </section>
      ))}

      <section className="section">
        <div className="card">
          <h2 className="card-title">Backend connection</h2>
          <dl className="def-list">
            <dt>Dev proxy target</dt>
            <dd><code>http://localhost:8000</code></dd>
            <dt>API prefix</dt>
            <dd><code>/api</code></dd>
            <dt>CORS origins</dt>
            <dd><code>localhost:3000</code>, <code>localhost:5173</code></dd>
          </dl>
        </div>
      </section>
    </div>
  )
}
