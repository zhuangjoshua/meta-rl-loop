#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

function usage() {
  console.error("Usage: run_workflow.js <runtime-input.json> [output-dir]");
  process.exit(1);
}

function runNode(scriptPath, args) {
  const result = spawnSync(process.execPath, [scriptPath, ...args], {
    cwd: process.cwd(),
    encoding: "utf8",
    env: process.env,
  });

  if (result.status !== 0) {
    throw new Error(result.stderr.trim() || result.stdout.trim() || `Failed: ${scriptPath}`);
  }
  return result.stdout;
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function main() {
  const runtimePath = process.argv[2];
  const outputDir = process.argv[3] || path.resolve("product", "lightreel-seedance-fal-ugc-run");

  if (!runtimePath) {
    usage();
  }

  const resolvedOutputDir = path.resolve(outputDir);
  ensureDir(resolvedOutputDir);

  const here = __dirname;
  const promptPath = path.join(resolvedOutputDir, "lightreel-prompt.txt");
  const conversationPath = path.join(resolvedOutputDir, "lightreel-conversation.json");
  const seedancifiedPath = path.join(resolvedOutputDir, "seedancified.json");
  const falPayloadPath = path.join(resolvedOutputDir, "fal-payload.json");
  const receiptPath = path.join(resolvedOutputDir, "run-receipt.json");

  const prompt = runNode(path.join(here, "build_lightreel_prompt.js"), [runtimePath]);
  fs.writeFileSync(promptPath, prompt);

  runNode(path.join(here, "query_lightreel.js"), [promptPath, conversationPath]);
  runNode(path.join(here, "seedancify.js"), [runtimePath, conversationPath, seedancifiedPath]);
  runNode(path.join(here, "build_fal_payload.js"), [runtimePath, seedancifiedPath, falPayloadPath]);

  const receipt = {
    runtime_input: path.resolve(runtimePath),
    lightreel_prompt: promptPath,
    lightreel_conversation: conversationPath,
    seedancified_output: seedancifiedPath,
    fal_payload: falPayloadPath,
    generated_at: new Date().toISOString(),
  };

  fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2));
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
}

main();
