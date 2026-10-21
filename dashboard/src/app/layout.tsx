import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";
import { AppShell } from "@/components/app-shell";
import { ThemeProvider } from "@/components/theme-provider";
import { ToastProvider } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono" });

const themeScript = `(function(){try{var t=localStorage.getItem("veinore-theme");var dark=t==="dark";document.documentElement.classList.toggle("dark",dark);document.documentElement.style.colorScheme=dark?"dark":"light";}catch(e){}})();`;

export const metadata: Metadata = {
  metadataBase: new URL("http://127.0.0.1:3000"),
  title: {
    default: "Veinore",
    template: "%s · Veinore",
  },
  description: "Veinore is the local workspace dashboard for the evidence-first Lead Research Kit.",
  applicationName: "Veinore",
  icons: {
    icon: [
      { url: "/favicon-16.png", sizes: "16x16", type: "image/png" },
      { url: "/favicon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  openGraph: {
    title: "Veinore",
    description: "Evidence-first lead research",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "Veinore" }],
  },
};

export default function RootLayout({ children }: LayoutProps<"/">): React.ReactElement {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={cn("h-full antialiased", inter.variable, geistMono.variable, "font-sans")}
    >
      <body className="relative min-h-full bg-background text-foreground">
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <div className="isolate relative flex min-h-svh flex-col">
          <ThemeProvider>
            <ToastProvider position="bottom-right">
              <AppShell>{children}</AppShell>
            </ToastProvider>
          </ThemeProvider>
        </div>
      </body>
    </html>
  );
}
