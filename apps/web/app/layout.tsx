import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geist = Geist({ variable: "--font-geist", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

/** Applies the saved appearance before first paint (same key as STLL REWARDS). */
const themeScript = `(function(){try{var p=localStorage.getItem("stll-theme")||"system";var m=window.matchMedia("(prefers-color-scheme: dark)");var r=p==="system"?(m.matches?"dark":"light"):p;document.documentElement.dataset.theme=r;}catch(e){}})();`;

export const metadata: Metadata = {
  title: { default: "STLL PAY", template: "%s · STLL PAY" },
  description: "Bank payments by QR code for New Zealand businesses, from STLL HAUS.",
  applicationName: "STLL PAY",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F5F3EF" },
    { media: "(prefers-color-scheme: dark)", color: "#121211" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en-NZ"
      className={`${geist.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-dvh bg-bg text-ink flex flex-col">{children}</body>
    </html>
  );
}
