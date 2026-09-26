import { QueryClientProvider } from "@tanstack/react-query";
import { render as rtlRender, type RenderOptions } from "@testing-library/react";
import * as React from "react";

import { makeQueryClient } from "@/app/providers";

/**
 * Renders with a real QueryClient, so loading, error and retry behaviour under
 * test is the behaviour that ships. Retries are off so an expected failure does
 * not take three round trips to appear.
 */
export function render(ui: React.ReactElement, options?: RenderOptions) {
  const client = makeQueryClient();
  client.setDefaultOptions({
    queries: { retry: false, staleTime: 0, refetchOnWindowFocus: false },
    mutations: { retry: false },
  });

  function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }

  return { client, ...rtlRender(ui, { wrapper: Wrapper, ...options }) };
}

export * from "@testing-library/react";
