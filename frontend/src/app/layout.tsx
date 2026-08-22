import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

/**
 * Archivo for voice, IBM Plex Mono for data. Both self-hosted from
 * ./fonts so the Docker build needs no network beyond npm.
 */
const archivo = localFont({
  src: "./fonts/Archivo-Variable.woff2",
  variable: "--font-archivo",
  weight: "400 800",
  display: "swap",
});

const plexMono = localFont({
  src: [
    { path: "./fonts/IBMPlexMono-Regular.woff2", weight: "400", style: "normal" },
    { path: "./fonts/IBMPlexMono-Medium.woff2", weight: "500", style: "normal" },
    { path: "./fonts/IBMPlexMono-SemiBold.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "KiCAD Mitos",
  description: "Natural-language batch editing for KiCAD, verified with KiCAD's own ERC.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${archivo.variable} ${plexMono.variable} antialiased`}>{children}</body>
    </html>
  );
}
