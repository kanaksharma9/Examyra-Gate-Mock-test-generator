"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { browserSupabase } from "../lib/supabase-browser";
const ThemeContext = createContext({});
export function Providers({ children }) {
  const [theme, setTheme] = useState("light");
  const [user, setUser] = useState(null);
  const supabase = browserSupabase();
  useEffect(() => setTheme(localStorage.getItem("theme") || "light"), []);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("theme", theme); }, [theme]);
  useEffect(() => {
    let mounted = true;
    supabase.auth.getUser().then(({ data }) => {
      if (mounted) setUser(data.user || null);
    });
    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user || null);
    });
    return () => {
      mounted = false;
      listener.subscription.unsubscribe();
    };
  }, [supabase]);
  return <ThemeContext.Provider value={{ theme, toggle: () => setTheme((x) => x === "light" ? "dark" : "light"), supabase, user }}>{children}</ThemeContext.Provider>;
}
export const useApp = () => useContext(ThemeContext);
