import "./globals.css";
import { BRAND } from "../lib/brand";
import { Providers } from "../components/Providers";
export const metadata = { title: BRAND.name, description: BRAND.tagline };
export default function RootLayout({ children }) { return <html lang="en"><body><Providers>{children}</Providers></body></html>; }
