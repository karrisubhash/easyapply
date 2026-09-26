import * as React from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export function LoadingCard({ label = "Loading" }: { label?: string }) {
  return (
    <Card>
      <CardContent className="space-y-3 p-6">
        <span className="sr-only" role="status">
          {label}
        </span>
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </CardContent>
    </Card>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      {action && <CardContent className="flex gap-2">{action}</CardContent>}
    </Card>
  );
}

/**
 * A page that stopped before its content: not found, loading, failed, empty.
 *
 * It still leads with the page's heading. An early return that rendered only a
 * card left the page without an h1, so a screen-reader user jumping to the
 * main heading — the usual first move on arrival — found nothing, and could
 * not tell which page had failed to load.
 */
export function PageState({
  heading,
  children,
}: {
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{heading}</h1>
      {children}
    </div>
  );
}
