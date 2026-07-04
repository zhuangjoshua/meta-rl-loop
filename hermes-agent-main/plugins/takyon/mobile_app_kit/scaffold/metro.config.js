// Default Expo Metro config. Kept minimal on purpose — the platform overwrites _takyon/ wholesale
// and the app must not add a second bundler pipeline.
const { getDefaultConfig } = require("expo/metro-config");

module.exports = getDefaultConfig(__dirname);
