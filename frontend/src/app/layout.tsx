import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { SystemStatus } from "./system-status";

export const metadata: Metadata = {
  title: "Dota 2 Match Analyzer",
  description: "Local Dota 2 match analysis dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <header className="app-header">
          <div className="app-header-inner">
            <Link className="app-brand" href="/">
              <span className="app-brand-mark" aria-hidden="true">D2</span>
              <span>
                <strong>Match Analyzer</strong>
                <small>local intelligence</small>
              </span>
            </Link>
            <nav className="app-nav" aria-label="Primary navigation">
              <Link href="/">Matches</Link>
              <Link href="/upcoming">Upcoming</Link>
              <Link href="/models">Models</Link>
              <Link href="/data">Data</Link>
            </nav>
            <SystemStatus />
          </div>
        </header>
        {children}
        <footer className="app-footer">
          <span>DOTA 2 MATCH ANALYZER</span>
          <span>LOCAL / TIER 1 / SELF-HOSTED</span>
        </footer>
      </body>
    </html>
  );
}
