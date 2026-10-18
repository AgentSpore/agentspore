"use client";

import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { sharedMessages } from "@/lib/i18n/shared";

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
      return <BoundaryFallback message={this.state.message} onRetry={() => this.setState({ hasError: false, message: "" })} />;
    }

    return this.props.children;
  }
}


function BoundaryFallback({ message, onRetry }: { message: string; onRetry: () => void }) {
  const tr = useTranslations(sharedMessages);
  return (
    <div className="flex flex-col items-center justify-center min-h-[200px] gap-3 text-center px-4">
      <span className="text-red-400 text-sm font-medium">{tr('somethingWentWrong')}</span>
      <span className="text-neutral-600 text-xs">{message === sharedMessages.en.unexpectedError ? tr('unexpectedError') : message}</span>
      <button onClick={onRetry} className="text-xs text-neutral-400 hover:text-white transition-colors underline">
        {tr('tryAgain')}
      </button>
    </div>
  );
}
