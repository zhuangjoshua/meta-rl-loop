import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { getEncryptionEnv } from "./env";

const algorithm = "aes-256-gcm";

export function encryptSecret(plaintext: string) {
  const { key } = getEncryptionEnv();
  const iv = randomBytes(12);
  const cipher = createCipheriv(algorithm, key, iv);
  const ciphertext = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, ciphertext]).toString("base64");
}

export function decryptSecret(encoded: string) {
  const { key } = getEncryptionEnv();
  const payload = Buffer.from(encoded, "base64");
  const iv = payload.subarray(0, 12);
  const tag = payload.subarray(12, 28);
  const ciphertext = payload.subarray(28);
  const decipher = createDecipheriv(algorithm, key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString("utf8");
}
