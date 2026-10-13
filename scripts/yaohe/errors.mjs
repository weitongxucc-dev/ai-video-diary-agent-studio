export class CliError extends Error {
  constructor(message, code = 2, details = {}) { super(message); this.code = code; this.details = details; }
}
