#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

function _stripQuotes(value) {
  const trimmed = String(value || "").trim();
  if (trimmed.length >= 2) {
    const first = trimmed[0];
    const last = trimmed[trimmed.length - 1];
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return trimmed.slice(1, -1);
    }
  }
  return trimmed;
}

function _parseEnvLine(line) {
  const trimmed = String(line || "").trim();
  if (!trimmed || trimmed.startsWith("#")) {
    return null;
  }
  const normalized = trimmed.startsWith("export ") ? trimmed.slice(7).trim() : trimmed;
  const idx = normalized.indexOf("=");
  if (idx <= 0) {
    return null;
  }
  const key = normalized.slice(0, idx).trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
    return null;
  }
  const rawValue = normalized.slice(idx + 1);
  return { key, value: _stripQuotes(rawValue) };
}

function candidateEnvFiles() {
  const files = [];
  const explicit = String(process.env.TAKYON_ENV_FILE || "").trim();
  if (explicit) {
    files.push(path.resolve(explicit));
  }
  const takyonHome = String(process.env.TAKYON_HOME || "").trim();
  if (takyonHome) {
    files.push(path.resolve(takyonHome, "secrets", ".env"));
    files.push(path.resolve(takyonHome, ".env"));
  }
  files.push(path.resolve(process.cwd(), ".env.local"));
  files.push(path.resolve(process.cwd(), ".env"));
  return [...new Set(files)];
}

function loadTakyonEnv() {
  for (const filePath of candidateEnvFiles()) {
    if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
      continue;
    }
    const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
    for (const line of lines) {
      const parsed = _parseEnvLine(line);
      if (!parsed) {
        continue;
      }
      if (!String(process.env[parsed.key] || "").trim()) {
        process.env[parsed.key] = parsed.value;
      }
    }
  }
}

function firstEnvValue(...keys) {
  for (const key of keys) {
    const value = String(process.env[key] || "").trim();
    if (value) {
      return value;
    }
  }
  return "";
}

module.exports = {
  candidateEnvFiles,
  firstEnvValue,
  loadTakyonEnv,
};
