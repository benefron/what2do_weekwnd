/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#fbf7f0",
        ink: "#26201a",
        muted: "#6f6357",
        line: "#e7ddcd",
        tangerine: { DEFAULT: "#f5623d", dark: "#d94a27" },
        forest: { DEFAULT: "#2f6b4f", soft: "#e4efe7" },
        berry: "#8a3d6b",
      },
      fontFamily: {
        display: ['"Fraunces"', "Georgia", "serif"],
        sans: ['"Inter"', "system-ui", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(38,32,26,0.06), 0 8px 24px -12px rgba(38,32,26,0.18)",
      },
      borderRadius: {
        xl2: "1.25rem",
      },
    },
  },
  plugins: [],
};
