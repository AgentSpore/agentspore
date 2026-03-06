"use client";

import { Component, ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  message: string;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, message: "" };
  }

  static getDerivedStateFromError(error: unknown): State {
    const message = error instanceof Error ? error.message : "Unexpected error";
    return { hasError: true, message };
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex flex-col items-center justify-center min-h-[200px] gap-3 text-center px-4">
          <span className="text-red-400 text-sm font-medium">Something went wrong</span>
          <span className="text-slate-600 text-xs">{this.state.message}</span>
          <button
            onClick={() => this.setState({ hasError: false, message: "" })}
            className="text-xs text-violet-400 hover:text-violet-300 transition-colors underline"
          >
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
