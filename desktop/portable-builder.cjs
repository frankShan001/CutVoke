module.exports = {
  ...require("./package.json").build,
  extraFiles: [{ from: "portable.json", to: "portable.json" }],
};
