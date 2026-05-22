import { NextResponse } from "next/server";
import { publicErrorMessage, statusForError } from "./errors";

export function jsonOk<T>(body: T, init?: ResponseInit) {
  return NextResponse.json(body, init);
}

export function jsonError(error: unknown) {
  return NextResponse.json(
    {
      ok: false,
      error: publicErrorMessage(error)
    },
    { status: statusForError(error) }
  );
}
