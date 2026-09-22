import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { Nav } from "@/components/nav";
import { Toaster } from "@/components/ui/sonner";
import { api } from "@/lib/api";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "GroundControl",
  description:
    "Operations copilot for Riverside Grounds - inbound job request to human-approved quote.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const stats = await api.stats();

  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="bg-background text-foreground flex min-h-full flex-col">
        <Nav pendingCount={stats?.pending_approval} />
        <div className="mx-auto w-full max-w-[1600px] flex-1 px-6 py-6">{children}</div>
        <Toaster position="bottom-right" />
      </body>
    </html>
  );
}
