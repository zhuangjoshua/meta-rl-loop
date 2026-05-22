import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Takyon",
  description: "Polsia/Takyon company builder"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
