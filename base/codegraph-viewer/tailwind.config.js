/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  safelist: [
    { pattern: /^bg-(gray|zinc|slate|stone)-(700|800|900|950)/ },
    { pattern: /^border-(gray|zinc|slate|stone)-(700|800)/ },
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
