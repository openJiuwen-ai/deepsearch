import { createContext, useContext, useEffect, useState } from "react";

export interface ThemeColors {
  /** Page / body background */
  pageBg: string;
  /** Tailwind class for page bg (applied to body) */
  pageBgClass: string;
  /** Header bar background */
  headerBg: string;
  /** Panel / card backgrounds (filters, detail, legend) */
  panelBg: string;
  /** Semi-transparent panel variant */
  panelBgAlpha: string;
  /** Input / secondary surface */
  surfaceBg: string;
  /** Border color */
  border: string;
  /** ForceGraph canvas hex background */
  canvasBg: string;
  /** Visible swatch color for the theme picker preview */
  swatch: string;
}

export interface Theme {
  id: string;
  label: string;
  colors: ThemeColors;
}

export const THEMES: Theme[] = [
  {
    id: "midnight",
    label: "Midnight",
    colors: {
      pageBg: "#111827",
      pageBgClass: "bg-gray-900",
      headerBg: "bg-gray-800",
      panelBg: "bg-gray-800",
      panelBgAlpha: "bg-gray-800/95",
      surfaceBg: "bg-gray-700",
      border: "border-gray-700",
      canvasBg: "#111827",
      swatch: "#4b5563",
    },
  },
  {
    id: "charcoal",
    label: "Charcoal",
    colors: {
      pageBg: "#18181b",
      pageBgClass: "bg-zinc-900",
      headerBg: "bg-zinc-800",
      panelBg: "bg-zinc-800",
      panelBgAlpha: "bg-zinc-800/95",
      surfaceBg: "bg-zinc-700",
      border: "border-zinc-700",
      canvasBg: "#18181b",
      swatch: "#52525b",
    },
  },
  {
    id: "ocean",
    label: "Ocean",
    colors: {
      pageBg: "#0a1e2a",
      pageBgClass: "bg-[#0a1e2a]",
      headerBg: "bg-[#0f2d3d]",
      panelBg: "bg-[#0f2d3d]",
      panelBgAlpha: "bg-[#0f2d3d]/95",
      surfaceBg: "bg-[#164050]",
      border: "border-[#1a5060]",
      canvasBg: "#0a1e2a",
      swatch: "#0d9488",
    },
  },
  {
    id: "abyss",
    label: "Abyss",
    colors: {
      pageBg: "#030712",
      pageBgClass: "bg-gray-950",
      headerBg: "bg-gray-900",
      panelBg: "bg-gray-900",
      panelBgAlpha: "bg-gray-900/95",
      surfaceBg: "bg-gray-800",
      border: "border-gray-800",
      canvasBg: "#030712",
      swatch: "#1f2937",
    },
  },
  {
    id: "warm",
    label: "Warm Night",
    colors: {
      pageBg: "#1c1917",
      pageBgClass: "bg-stone-900",
      headerBg: "bg-stone-800",
      panelBg: "bg-stone-800",
      panelBgAlpha: "bg-stone-800/95",
      surfaceBg: "bg-stone-700",
      border: "border-stone-700",
      canvasBg: "#1c1917",
      swatch: "#78716c",
    },
  },
];

const STORAGE_KEY = "jcp-theme";

const defaultTheme = THEMES[0];

function loadTheme(): Theme {
  try {
    const id = localStorage.getItem(STORAGE_KEY);
    return THEMES.find((t) => t.id === id) ?? defaultTheme;
  } catch {
    return defaultTheme;
  }
}

interface ThemeContextValue {
  theme: Theme;
  setThemeById: (id: string) => void;
}

const ThemeCtx = createContext<ThemeContextValue>({
  theme: defaultTheme,
  setThemeById: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(loadTheme);

  const setThemeById = (id: string) => {
    const t = THEMES.find((th) => th.id === id);
    if (t) setTheme(t);
  };

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, theme.id);
    document.body.style.backgroundColor = theme.colors.pageBg;
  }, [theme]);

  return (
    <ThemeCtx.Provider value={{ theme, setThemeById }}>
      {children}
    </ThemeCtx.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeCtx);
}
