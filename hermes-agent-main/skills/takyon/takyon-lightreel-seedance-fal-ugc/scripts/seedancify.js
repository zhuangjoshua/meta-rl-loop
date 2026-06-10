#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

function usage() {
  console.error("Usage: seedancify.js <runtime-input.json> <lightreel-conversation.json> [output-json]");
  process.exit(1);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(path.resolve(filePath), "utf8"));
}

function extractAssistantAnswer(conversation) {
  const messages = conversation.messages || [];
  const assistant = [...messages].reverse().find((message) => message.role === "assistant");
  if (!assistant || typeof assistant.answer !== "string") {
    throw new Error("Unable to find a string assistant answer in Lightreel conversation");
  }
  return assistant.answer;
}

function extractQuotedScript(answer) {
  const curlyMatches = answer.match(/“([^”]+)”/g) || [];
  const straightMatches = answer.match(/"([^"]+)"/g) || [];
  const matches = [...curlyMatches, ...straightMatches];
  if (!matches.length) {
    throw new Error("Unable to extract spoken script from Lightreel answer");
  }
  const longest = matches
    .map((match) => match.replace(/^["“]|["”]$/g, ""))
    .sort((a, b) => b.length - a.length)[0];
  return longest;
}

function buildSeedancePrompt(runtimeInput, spokenScript) {
  const { company, creative_constraints: constraints } = runtimeInput;
  const framing = constraints.camera_mode || "handheld iPhone selfie";
  return `Use @Image1 as a reference-only identity anchor for the main subject, not as a start frame to recreate literally. Preserve the same face, age, skin tone, hair, outfit, and overall look from @Image1 throughout the clip while generating a fresh natural performance. Create a ${constraints.duration_seconds}-second vertical ${constraints.aspect_ratio} UGC video in ${framing} style. One creator only. Direct-to-camera the entire time. Tight chest-up framing, natural daylight, subtle handheld micro-shake, authentic creator energy, native creator pacing, and no polished brand feel.

Tone: preserve the hook energy and format discovered by Lightreel for ${company.name}. Keep it creator-native, spoken, and believable.

Spoken dialogue exactly:
"${spokenScript}"

Hard constraints:
No product shots. No UI. No screen recordings. No cutaways. No on-screen text. No captions. No overlay graphics. No second person. No logos in frame. No face drift. No outfit drift. No background morphing. Do not treat @Image1 as a literal first frame composition. Use it only for subject identity and look consistency.`;
}

function main() {
  const runtimePath = process.argv[2];
  const conversationPath = process.argv[3];
  const outputPath = process.argv[4];

  if (!runtimePath || !conversationPath) {
    usage();
  }

  const runtimeInput = readJson(runtimePath);
  const conversation = readJson(conversationPath);
  const assistantAnswer = extractAssistantAnswer(conversation);
  const spokenScript = extractQuotedScript(assistantAnswer);
  const seedancePrompt = buildSeedancePrompt(runtimeInput, spokenScript);

  const output = {
    company: runtimeInput.company.name,
    spoken_script: spokenScript,
    lightreel_answer: assistantAnswer,
    seedance_prompt: seedancePrompt,
  };

  const serialized = JSON.stringify(output, null, 2);
  if (outputPath) {
    fs.writeFileSync(path.resolve(outputPath), serialized);
  } else {
    process.stdout.write(`${serialized}\n`);
  }
}

main();
