import { useState } from "react";
import { THEMES, useTheme } from "../ThemeContext";

export default function ThemePicker() {
  const { theme, setThemeById } = useTheme();
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((p) => !p)}
        className="px-3 py-1 text-sm rounded bg-gray-700 text-gray-300 hover:bg-gray-600 flex items-center gap-1.5"
      >
        <span
          className="w-3 h-3 rounded-full border border-gray-500 inline-block"
          style={{ backgroundColor: theme.colors.swatch }}
        />
        Theme
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 bg-gray-800 border border-gray-600 rounded-lg shadow-xl p-2 z-50 min-w-[160px]">
          {THEMES.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                setThemeById(t.id);
                setOpen(false);
              }}
              className={`w-full flex items-center gap-2.5 px-3 py-1.5 rounded text-sm transition ${
                t.id === theme.id
                  ? "bg-indigo-600 text-white"
                  : "text-gray-300 hover:bg-gray-700"
              }`}
            >
              <span
                className="w-4 h-4 rounded-full border border-gray-500 inline-block flex-shrink-0"
                style={{ backgroundColor: t.colors.swatch }}
              />
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
