import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";
import { AppShell } from "@/components/app-shell";
import { ToastProvider } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });
const interHeading = Inter({ subsets: ["latin"], variable: "--font-heading" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "Lead Research",
  description: "Workspace dashboard for the evidence-first Lead Research Kit",
};

export default function RootLayout({ children }: LayoutProps<"/">): React.ReactElement {
  return (
    <html
      lang="en"
      className={cn("dark h-full antialiased", inter.variable, interHeading.variable, geistMono.variable, "font-sans")}
    >
      <body className="min-h-full bg-background text-foreground">
        <ToastProvider position="bottom-right">
          <AppShell>{children}</AppShell>
        </ToastProvider>
      </body>
    </html>
  );
}
