"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { EmptyState, LoadingCard } from "@/components/profile/states";
import { ProcessingStatusBadge } from "@/components/resume/processing-status";
import { ResumeUpload } from "@/components/resume/resume-upload";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useResumes } from "@/lib/api/resume-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

export default function ResumePage() {
  const router = useRouter();
  const resumes = useResumes();

  usePageTitle("Resume");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Resume</h1>
        <p className="text-sm text-muted-foreground">
          Upload a resume and EasyApply will read it into a draft profile version for you to check.
        </p>
      </div>

      <ResumeUpload onUploaded={(id) => router.push(`/resume/${id}`)} />

      {resumes.isPending ? <LoadingCard label="Loading resumes" /> : null}
      {resumes.isError ? (
        <ApiErrorAlert error={resumes.error} onRetry={() => void resumes.refetch()} />
      ) : null}

      {resumes.isSuccess && resumes.data.length === 0 ? (
        <EmptyState
          title="No resumes yet"
          description="Upload one above to get started."
        />
      ) : null}

      {resumes.isSuccess && resumes.data.length > 0 ? (
        <ul className="space-y-3">
          {resumes.data.map((document) => (
            <li key={document.id}>
              <Card>
                <CardContent className="flex items-center justify-between gap-4 p-4">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium">
                      <span className="truncate">{document.original_filename}</span>
                      <ProcessingStatusBadge run={document.latest_run} />
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Uploaded {formatDateTime(document.created_at)} ·{" "}
                      {Math.max(1, Math.round(document.byte_size / 1024))} KB
                    </p>
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    <Link
                      href={`/resume/${document.id}`}
                      aria-label={`View ${document.original_filename}`}
                    >
                      View
                    </Link>
                  </Button>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
