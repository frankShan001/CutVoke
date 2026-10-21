module.exports = {
  ...require("./package.json").build,
  mac: {
    icon: "assets/brand/cutvoke.icns",
    category: "public.app-category.video",
    minimumSystemVersion: "13.0.0",
    identity: "-", // Local ad-hoc integrity signature; no Developer ID or notarization.
    hardenedRuntime: false,
    gatekeeperAssess: false,
    notarize: false,
  },
  linux: { executableName: "cutvoke", category: "AudioVideo", icon: "assets/brand/icon-256.png" },
};
