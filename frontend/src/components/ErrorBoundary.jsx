import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from './ui/button';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-[#0F172A] flex items-center justify-center p-6" data-testid="error-boundary">
          <div className="text-center max-w-md">
            <div className="w-16 h-16 bg-red-900/30 rounded-2xl flex items-center justify-center mx-auto mb-5 border border-red-800/40">
              <AlertTriangle className="w-8 h-8 text-red-400" />
            </div>
            <h1 className="text-white text-2xl font-bold mb-2" style={{ fontFamily: 'Manrope, sans-serif' }}>Something went wrong</h1>
            <p className="text-slate-400 text-sm mb-6 leading-relaxed">
              An unexpected error occurred. This has been logged automatically. Try refreshing the page.
            </p>
            <Button
              onClick={() => window.location.reload()}
              className="bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-xl px-6"
              data-testid="error-boundary-reload"
            >
              <RefreshCw className="w-4 h-4 mr-2" />
              Reload Page
            </Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
