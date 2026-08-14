/** @type {import("next").NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The shared packages ship TypeScript source rather than a build step, so
  // Next compiles them as part of the app.
  transpilePackages: ["@clinchec/shared-types", "@clinchec/fhir-client"],
  // Standalone output keeps the production image to the app plus its actual
  // runtime dependencies instead of the whole monorepo node_modules.
  output: "standalone",
  experimental: {
    typedRoutes: true,
    // Next 14 keeps this under `experimental`; it moves to the top level in 15.
    outputFileTracingRoot: require("path").join(__dirname, "../../"),
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          // PHI must never land in a shared or browser cache.
          { key: "Cache-Control", value: "no-store, max-age=0" },
        ],
      },
    ];
  },
};

module.exports = nextConfig;