export class ConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigurationError";
  }
}

export class IntegrationNotConfiguredError extends ConfigurationError {
  constructor(integration: string) {
    super(`${integration} is not configured.`);
    this.name = "IntegrationNotConfiguredError";
  }
}

export class IntegrationCallError extends Error {
  status?: number;

  constructor(integration: string, message: string, status?: number) {
    super(`${integration}: ${message}`);
    this.name = "IntegrationCallError";
    this.status = status;
  }
}

export class UnauthorizedError extends Error {
  constructor(message = "Authentication required.") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

export class ForbiddenError extends Error {
  constructor(message = "Not allowed.") {
    super(message);
    this.name = "ForbiddenError";
  }
}

export class NotFoundError extends Error {
  constructor(message = "Not found.") {
    super(message);
    this.name = "NotFoundError";
  }
}

export class BadRequestError extends Error {
  constructor(message = "Invalid request.") {
    super(message);
    this.name = "BadRequestError";
  }
}

export class RateLimitError extends Error {
  constructor(message = "Too many requests. Try again later.") {
    super(message);
    this.name = "RateLimitError";
  }
}

function isValidationError(error: unknown) {
  return Boolean(error && typeof error === "object" && "issues" in error && Array.isArray((error as { issues?: unknown }).issues));
}

export function statusForError(error: unknown) {
  if (error instanceof BadRequestError) return 400;
  if (isValidationError(error)) return 400;
  if (error instanceof UnauthorizedError) return 401;
  if (error instanceof ForbiddenError) return 403;
  if (error instanceof NotFoundError) return 404;
  if (error instanceof RateLimitError) return 429;
  if (error instanceof ConfigurationError) return 503;
  if (error instanceof IntegrationCallError) return error.status && error.status >= 400 && error.status < 600 ? error.status : 502;
  return 500;
}

export function publicErrorMessage(error: unknown) {
  if (
    error instanceof BadRequestError ||
    error instanceof UnauthorizedError ||
    error instanceof ForbiddenError ||
    error instanceof NotFoundError ||
    error instanceof RateLimitError ||
    error instanceof ConfigurationError ||
    error instanceof IntegrationCallError
  ) {
    return error.message;
  }

  if (isValidationError(error)) return "Invalid request.";

  return "Unexpected server error.";
}
