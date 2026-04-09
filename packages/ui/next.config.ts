import type { NextConfig } from "next";

const isExport = process.env.NEXT_OUTPUT === "export";

const nextConfig: NextConfig = {
  // Static export for production — served by the FastAPI gateway directly.
  // Dev mode keeps rewrites for the API proxy.
  ...(isExport
    ? { output: "export" }
    : {
        async rewrites() {
          return [
            { source: "/api/:path*", destination: "http://localhost:8001/api/:path*" },
          ];
        },
      }),
};

export default nextConfig;
