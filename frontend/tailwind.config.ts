import type { Config } from "tailwindcss";

/**
 * Tokens come from KiCAD's own canvas: schematic wire teal, component
 * red-brown, and board copper, set on cool drafting paper. Defined as CSS
 * variables in globals.css so the palette has one source of truth.
 */
const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: "var(--ink)",
        paper: "var(--paper)",
        sheet: "var(--sheet)",
        rule: "var(--rule)",
        muted: "var(--muted)",
        wire: "var(--wire)",
        "wire-soft": "var(--wire-soft)",
        brick: "var(--brick)",
        "brick-soft": "var(--brick-soft)",
        copper: "var(--copper)",
        "copper-line": "var(--copper-line)",
        "copper-soft": "var(--copper-soft)",
        background: "var(--paper)",
        foreground: "var(--ink)",
      },
      fontFamily: {
        sans: ["var(--font-archivo)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-plex-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        DEFAULT: "3px",
        sm: "2px",
        lg: "4px",
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      maxWidth: {
        bench: "84rem",
      },
    },
  },
  plugins: [],
};
export default config;
