"use client";

import { ButtonLink } from "@/components/ui";
import { usePublicConfig } from "@/lib/useConfig";

/** Sign-up is offered only once STLL PAY opens to other businesses. */
export function HomeActions() {
  const config = usePublicConfig();
  const open = config?.multi_merchant_enabled === true;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-3">
        {open && <ButtonLink href="/signup">Create a business account</ButtonLink>}
        <ButtonLink href="/login" variant={open ? "secondary" : "primary"}>
          Log in
        </ButtonLink>
      </div>
      {config && !open && (
        <p className="text-[14px] text-ink-2">
          STLL PAY is currently open to STLL HAUS only. Staff join by invitation.
        </p>
      )}
    </div>
  );
}
