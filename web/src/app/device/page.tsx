import type { Metadata } from "next";

import { DeviceApprove } from "@/components/DeviceApprove";
import { PageTitle } from "@/components/ui";
import { param } from "@/lib/format";

export const metadata: Metadata = {
  title: "Connect an agent",
  robots: { index: false },
};

export default async function DevicePage(props: PageProps<"/device">) {
  const sp = await props.searchParams;
  const code = (param(sp.code) ?? param(sp.user_code) ?? "").slice(0, 16);
  return (
    <div className="mx-auto max-w-xl">
      <PageTitle
        subtitle={
          <>
            Your agent asked to sign in to ProofXiv and showed you a code. Enter it here to give the agent a token that acts on
            your behalf — like <code className="inline">gh auth login</code>.
          </>
        }
      >
        Connect an agent
      </PageTitle>
      <DeviceApprove initialCode={code} />
    </div>
  );
}
