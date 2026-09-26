"use client";

import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/**
 * The state that blocks both matching and tailoring.
 *
 * Written once and used by both pages, because it is one fact about the user's
 * account and two different sentences for it would be two chances to describe
 * it wrongly. Both endpoints answer 409 without a confirmed version, so a page
 * showing this must also withhold its primary action — an enabled button whose
 * only possible outcome is an error is worse than no button.
 *
 * The reason is stated, not just the rule. "Confirm a version first" tells the
 * user what to type; "a draft can still change, which would make the result
 * meaningless" tells them why the product is like this, which is the difference
 * between a rule that feels arbitrary and one that feels careful.
 */
export function NoConfirmedVersion({ action }: { action: "matched" | "tailored" }) {
  return (
    <Alert>
      <AlertTitle>You have no confirmed profile version yet</AlertTitle>
      <AlertDescription>
        <p>
          Only a confirmed version can be {action}. A draft can still change, which would
          make the result meaningless the moment you edited it.
        </p>
        <div className="mt-3">
          <Button variant="outline" size="sm" asChild>
            <Link href="/profile">Go to profile</Link>
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
