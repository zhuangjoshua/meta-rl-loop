#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function formatFacts(company) {
  const lines = [];
  if (company.mechanism) {
    lines.push(company.mechanism);
  }
  if (Array.isArray(company.differentiators) && company.differentiators.length) {
    lines.push(`Differentiators: ${company.differentiators.join("; ")}`);
  }
  if (Array.isArray(company.proof) && company.proof.length) {
    lines.push(`Proof: ${company.proof.join("; ")}`);
  }
  return lines.join(" ");
}

function buildPrompt(runtimeInput) {
  const { company, creative_constraints: constraints } = runtimeInput;
  const toneFlags = [];

  if (constraints.allow_funny_or_irreverent) {
    toneFlags.push("funny or irreverent");
  }
  if (constraints.allow_lightly_skitty) {
    toneFlags.push("lightly skitty");
  }
  if (constraints.allow_chaotic) {
    toneFlags.push("chaotic");
  }
  if (constraints.allow_confessional !== false) {
    toneFlags.push("confessional");
  }

  const summaryBits = [
    `${company.name}, a ${company.category}`,
    `for ${company.audience}`,
    company.core_pain ? `solving ${company.core_pain}` : "",
  ].filter(Boolean);
  const facts = formatFacts(company);
  const toneClause = toneFlags.length ? `Tone can be ${toneFlags.join(", ")}.` : "";

  return [
    `Find a high-performing UGC talking-head format for ${summaryBits.join(" ")}.`,
    facts ? `Key facts: ${facts}.` : "",
    `Give me the format, hook logic, one spoken script, and one Seedance-ready prompt for a ${constraints.duration_seconds} second one-person selfie-style ad.`,
    `No UI, no product shots, no screen recordings, no text overlays, no cutaways, and no second person.`,
    toneClause,
  ]
    .filter(Boolean)
    .join(" ");
}

function main() {
  const inputPath = process.argv[2];
  if (!inputPath) {
    console.error("Usage: build_lightreel_prompt.js <runtime-input.json>");
    process.exit(1);
  }

  const runtimeInput = readJson(path.resolve(inputPath));
  const prompt = buildPrompt(runtimeInput);
  process.stdout.write(`${prompt}\n`);
}

main();
