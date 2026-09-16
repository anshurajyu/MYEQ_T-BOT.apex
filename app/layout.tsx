import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MYEQUATION T-BOT | Robot Control System",
  description: "Local MYEQUATION T-BOT control station.",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
