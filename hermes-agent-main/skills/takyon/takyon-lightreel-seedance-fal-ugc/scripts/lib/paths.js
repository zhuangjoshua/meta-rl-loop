"use strict";

const path = require("path");

/**
 * Resolve `filePath` against the current working directory and reject any
 * result that escapes the CWD (via `../` or an absolute path outside it).
 *
 * Throws an Error when the resolved path would escape the working directory.
 * Callers that prefer a null sentinel should wrap this at the call site.
 */
function resolveWithinCwd(filePath) {
  const root = path.resolve(process.cwd());
  const resolved = path.resolve(root, filePath);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) {
    throw new Error(`Refusing to use path outside working directory: ${filePath}`);
  }
  return resolved;
}

module.exports = { resolveWithinCwd };
