import type { Metadata } from "next";

import { OwnerPanel } from "@/components/owner/OwnerPanel";

export const metadata: Metadata = {
  title: "Owner panel",
  robots: { index: false },
};

export default function OwnerPage() {
  return <OwnerPanel />;
}
