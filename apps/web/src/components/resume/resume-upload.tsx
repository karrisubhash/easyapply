"use client";

import { ACCEPTED_RESUME_TYPES } from "@easyapply/shared";
import { Upload } from "lucide-react";
import * as React from "react";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useUploadResume } from "@/lib/api/resume-queries";

/**
 * Upload is deliberately dumb about what is acceptable.
 *
 * The `accept` attribute is a file-picker convenience, not a check — the server
 * sniffs the bytes and is the only thing entitled to an opinion. Re-implementing
 * the format rules here would give us two answers that could disagree, and the
 * one in the browser is the one an attacker skips anyway.
 */
export function ResumeUpload({ onUploaded }: { onUploaded?: (id: number) => void }) {
  const upload = useUploadResume();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = React.useState<string | null>(null);

  const handleFile = (file: File | undefined) => {
    if (!file) return;
    setFileName(file.name);
    // Clear the input before the request, not after. A file input fires `change`
    // only when the selection differs from what it already holds, so leaving the
    // name in place means picking the SAME file again — the obvious thing to do
    // after a failed upload — does nothing at all, with no feedback.
    if (inputRef.current) inputRef.current.value = "";
    upload.mutate(file, {
      onSuccess: (document) => onUploaded?.(document.id),
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Upload a resume</CardTitle>
        <CardDescription>
          PDF or DOCX. We read it, interpret it, and prepare a draft profile version for you to
          review — nothing is saved to your profile until you confirm it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_RESUME_TYPES.join(",")}
          className="sr-only"
          aria-label="Resume file"
          onChange={(event) => handleFile(event.target.files?.[0])}
        />

        <div className="flex items-center gap-3">
          <Button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={upload.isPending}
          >
            <Upload aria-hidden="true" className="size-4" />
            {upload.isPending ? "Processing…" : "Choose a file"}
          </Button>
          {fileName && (
            <span className="text-sm text-muted-foreground" data-testid="chosen-file">
              {fileName}
            </span>
          )}
        </div>

        {upload.isPending && (
          <p role="status" className="text-sm text-muted-foreground">
            Uploading and processing. This can take a few seconds.
          </p>
        )}

        {upload.isError ? <ApiErrorAlert error={upload.error} /> : null}
      </CardContent>
    </Card>
  );
}
