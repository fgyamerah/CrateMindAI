import { Component, ReactNode } from 'react'

interface Props { children: ReactNode; fallback?: ReactNode }
interface State { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  render() {
    if (this.state.error) {
      return this.props.fallback ?? (
        <div style={{ padding: 32, color: '#ef4444', fontFamily: 'monospace' }}>
          <strong>Page crashed:</strong> {this.state.error.message}
          <br />
          <button
            style={{ marginTop: 12, padding: '4px 12px', cursor: 'pointer' }}
            onClick={() => this.setState({ error: null })}
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
