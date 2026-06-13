// The one place the Takyon kit is constructed. All app code imports `client`
// from here; nothing else may import from _takyon/ directly except this file
// (the platform overwrites _takyon/ wholesale in real products).
import { createSubuserRuntimeClient } from "@takyon/runtime-client.js";
import { surfaceContext } from "@takyon/surface-context.js";

export const client = createSubuserRuntimeClient(surfaceContext);

export type TakyonClient = typeof client;

/** Errors thrown by the kit's action runner carry classification metadata. */
export interface TakyonActionError extends Error {
  kind?: string;
  status?: number;
  checkoutUrl?: string;
  rail?: string;
  railState?: string;
}
