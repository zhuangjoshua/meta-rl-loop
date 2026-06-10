#!/usr/bin/env node

const fs = require("fs");
const https = require("https");
const path = require("path");
const { firstEnvValue, loadTakyonEnv } = require("./env");

function usage() {
  console.error("Usage: query_lightreel.js <prompt-file> [output-json]");
  process.exit(1);
}

function requestJson(url, options, body) {
  return new Promise((resolve, reject) => {
    const req = https.request(url, options, (res) => {
      let data = "";
      res.on("data", (chunk) => {
        data += chunk;
      });
      res.on("end", () => {
        try {
          resolve({
            status: res.statusCode,
            json: JSON.parse(data),
          });
        } catch (error) {
          reject(new Error(`Invalid JSON response: ${error.message}`));
        }
      });
    });

    req.on("error", reject);
    req.on("timeout", () => req.destroy(new Error("Lightreel request timed out")));
    if (body) {
      req.write(body);
    }
    req.end();
  });
}

async function pollConversation(conversationId, apiKey) {
  const maxAttempts = 60;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const response = await requestJson(
      `https://api.lightreel.ai/v1/chat/${conversationId}`,
      {
        method: "GET",
        headers: {
          Authorization: `Bearer ${apiKey}`,
        },
        timeout: 30000,
      },
    );

    if (response.status >= 400) {
      throw new Error(response.json?.error?.message || `Lightreel polling failed with status ${response.status}`);
    }

    const messages = response.json?.messages || [];
    const assistantMessage = [...messages].reverse().find((message) => message.role === "assistant");
    if (assistantMessage) {
      return response.json;
    }

    await new Promise((resolve) => setTimeout(resolve, 5000));
  }

  throw new Error("Lightreel conversation did not complete within the polling window");
}

async function main() {
  const promptPath = process.argv[2];
  const outputPath = process.argv[3];

  if (!promptPath) {
    usage();
  }

  loadTakyonEnv();
  const apiKey = firstEnvValue("LIGHTREEL_API_KEY");
  if (!apiKey) {
    console.error("Missing LIGHTREEL_API_KEY");
    process.exit(1);
  }

  const question = fs.readFileSync(path.resolve(promptPath), "utf8").trim();
  const body = JSON.stringify({ question });
  const submit = await requestJson(
    "https://api.lightreel.ai/v1/chat",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
      timeout: 240000,
    },
    body,
  );

  if (submit.status >= 400) {
    throw new Error(submit.json?.error?.message || `Lightreel submit failed with status ${submit.status}`);
  }

  const conversationId = submit.json?.conversationId;
  if (!conversationId) {
    throw new Error("Lightreel did not return a conversationId");
  }

  const finalConversation = await pollConversation(conversationId, apiKey);
  const serialized = JSON.stringify(finalConversation, null, 2);

  if (outputPath) {
    fs.writeFileSync(path.resolve(outputPath), serialized);
  } else {
    process.stdout.write(`${serialized}\n`);
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
