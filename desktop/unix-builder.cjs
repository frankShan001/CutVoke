module.exports = {
  ...require("./package.json").build,
  mac: {
    category: "public.app-category.video",
    minimumSystemVersion: "13.0.0",
    identity: "-", // Local ad-hoc integrity signature; no Developer ID or notarization.
    hardenedRuntime: false,
    gatekeeperAssess: false,
    notarize: false,
  },
  linux: { executableName: "cutvoke", category: "AudioVideo" },
};
