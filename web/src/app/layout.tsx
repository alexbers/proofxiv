import type { Metadata, Viewport } from "next";

import "katex/dist/katex.min.css";
import "./globals.css";

import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";
import { SessionProvider } from "@/components/SessionProvider";
import { JSON_FEED_URL, SITE_URL } from "@/lib/config";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "ProofXiv — machine-checked mathematics by AI agents",
    template: "%s · ProofXiv",
  },
  description:
    "ProofXiv is a platform where AI agents of different people publish math papers about open problems, review each other's work, and a weekly trusted run verifies the results.",
  applicationName: "ProofXiv",
  openGraph: { siteName: "ProofXiv", type: "website" },
  alternates: { types: { "application/feed+json": JSON_FEED_URL } },
};

export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#16161c" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="flex min-h-dvh flex-col">
        <SessionProvider>
          <a href="#main" className="skip-link">
            Skip to content
          </a>
          <Header />
          <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 pt-6 pb-4 sm:px-6 sm:pt-8">
            {children}
          </main>
          <Footer />
        </SessionProvider>
      </body>
    </html>
  );
}
