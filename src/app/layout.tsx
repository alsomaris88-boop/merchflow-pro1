import type { Metadata } from "next";
import type { ReactNode } from "react";
import "@fontsource/space-grotesk/400.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/600.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "MerchFlow Pro — POD Intelligence & Automation Suite",
  description:
    "All-in-one Print-on-Demand automation: zero-prompting design fusion, libvips-grade processing lab, USPTO trademark shield, dual-marketplace listing export and live trend radar for Amazon Merch on Demand & TeePublic.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-ink-950 text-[#e8e8f0] antialiased noise">{children}</body>
    </html>
  );
}
