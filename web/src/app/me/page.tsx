import type { Metadata } from "next";

import { MePanel } from "@/components/me/MePanel";

export const metadata: Metadata = {
  title: "Operator panel",
  robots: { index: false },
};

export default function MePage() {
  return <MePanel />;
}
