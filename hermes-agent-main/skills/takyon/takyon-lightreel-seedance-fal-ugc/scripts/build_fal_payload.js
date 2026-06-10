#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

function usage() {
  console.error("Usage: build_fal_payload.js <runtime-input.json> <seedancified.json> [output-json]");
  process.exit(1);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(path.resolve(filePath), "utf8"));
}

function main() {
  const runtimePath = process.argv[2];
  const seedancifiedPath = process.argv[3];
  const outputPath = process.argv[4];

  if (!runtimePath || !seedancifiedPath) {
    usage();
  }

  const runtimeInput = readJson(runtimePath);
  const seedancified = readJson(seedancifiedPath);
  const constraints = runtimeInput.creative_constraints;

  const payload = {
    model: "bytedance/seedance-2.0/reference-to-video",
    input: {
      prompt: seedancified.seedance_prompt,
      image_urls: [runtimeInput.reference_image.storage_url],
      duration: String(constraints.duration_seconds),
      resolution: constraints.resolution || "720p",
      aspect_ratio: constraints.aspect_ratio,
      generate_audio: constraints.generate_audio !== false,
    },
  };

  const serialized = JSON.stringify(payload, null, 2);
  if (outputPath) {
    fs.writeFileSync(path.resolve(outputPath), serialized);
  } else {
    process.stdout.write(`${serialized}\n`);
  }
}

main();
