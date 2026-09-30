import { Component } from 'react'

interface Props {
  children: React.ReactNode
  fallback?: React.ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error) {
    console.error('ErrorBoundary caught:', error)
  }

  private reload = () => window.location.reload()

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div className="flex flex-col items-center justify-center h-screen bg-gray-100 gap-4">
          <p className="text-lg text-red-600">
            Something went wrong{this.state.error ? `: ${this.state.error.message}` : '.'}
          </p>
          <button
            onClick={this.reload}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 cursor-pointer"
          >
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
