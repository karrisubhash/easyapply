"use client";

import * as React from "react";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useConfirmVersion } from "@/lib/api/queries";

/**
 * Confirmation is irreversible — the database refuses to reopen a confirmed
 * version — so it asks first and says plainly what will happen.
 */
export function ConfirmVersionButton({ versionNumber }: { versionNumber: number }) {
  const [open, setOpen] = React.useState(false);
  const [makeCurrent, setMakeCurrent] = React.useState(true);
  const confirm = useConfirmVersion(versionNumber);

  return (
    <>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogTrigger asChild>
          <Button>Confirm version</Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm version {versionNumber}?</AlertDialogTitle>
            <AlertDialogDescription>
              This freezes the version permanently. It cannot be edited or reopened afterwards. To
              change your career data later, you create a new draft.
            </AlertDialogDescription>
          </AlertDialogHeader>

          <label className="mt-4 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={makeCurrent}
              onChange={(event) => setMakeCurrent(event.target.checked)}
            />
            Make this the current version
          </label>
          <p className="text-xs text-muted-foreground">
            The current version is what your profile page shows and what matching and tailoring
            start from. You can also make a confirmed version current later, from its page.
          </p>

          <AlertDialogFooter>
            <AlertDialogCancel disabled={confirm.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={confirm.isPending}
              onClick={(event) => {
                // Keep the dialog open until the request settles, so a failure
                // is visible instead of vanishing with the dialog.
                event.preventDefault();
                confirm.mutate(
                  { make_current: makeCurrent },
                  { onSuccess: () => setOpen(false) },
                );
              }}
            >
              {confirm.isPending ? "Confirming…" : "Confirm"}
            </AlertDialogAction>
          </AlertDialogFooter>

          {confirm.isError ? (
            <div className="mt-4">
              <ApiErrorAlert error={confirm.error} />
            </div>
          ) : null}
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
