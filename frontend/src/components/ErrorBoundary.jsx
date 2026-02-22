import React from 'react';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Alert, AlertDescription } from './ui/alert';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('React Error Boundary caught an error:', error, errorInfo);
    this.setState({ errorInfo });
  }

  handleReload = () => {
    window.location.reload();
  };

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-[100dvh] items-center justify-center px-4 py-10">
          <Card className="w-full max-w-lg border-border/60 bg-background/80">
            <CardHeader>
              <CardTitle className="text-2xl">Something went wrong</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                An unexpected error occurred. Please try reloading the page.
              </p>
              {this.state.error && (
                <Alert variant="destructive">
                  <AlertDescription>
                    <details className="text-sm">
                      <summary className="cursor-pointer font-medium">Error details</summary>
                      <pre className="mt-2 whitespace-pre-wrap text-xs">
                        {this.state.error.toString()}
                      </pre>
                    </details>
                  </AlertDescription>
                </Alert>
              )}
              <div className="flex flex-wrap gap-3">
                <Button variant="secondary" onClick={this.handleReset}>
                  Try Again
                </Button>
                <Button onClick={this.handleReload}>Reload Page</Button>
              </div>
            </CardContent>
          </Card>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
