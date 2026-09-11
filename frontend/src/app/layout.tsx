import type { Metadata } from "next";
import "./globals.css";
import { TerminalShell } from "@/components/terminal-shell";

export const metadata: Metadata = {
  title: "PokéQuant Terminal",
  description: "Quantitative alternative-asset terminal for Pokémon TCG graded slabs and sealed product",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <TerminalShell>{children}</TerminalShell>
      </body>
    </html>
  );
}
