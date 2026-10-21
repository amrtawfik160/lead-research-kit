import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Veinore",
    short_name: "Veinore",
    description: "Evidence-first lead research",
    start_url: "/",
    display: "standalone",
    background_color: "#08090a",
    theme_color: "#08090a",
    icons: [
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" },
      { src: "/favicon-32.png", sizes: "32x32", type: "image/png" },
    ],
  };
}
