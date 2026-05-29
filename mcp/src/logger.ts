type LogLevel = "info" | "warn" | "error";

const SENSITIVE_VALUE_RE =
	/(sk-[A-Za-z0-9_-]{12,}|postgres(?:ql)?:\/\/[^\s]+|DATABASE_URL=[^\s]+|OPENAI_API_KEY=[^\s]+)/g;

function redact(value: string): string {
	return value.replace(SENSITIVE_VALUE_RE, "[redacted]");
}

function toLogValue(value: unknown): string | number | boolean | null {
	if (value === null) {
		return null;
	}
	if (
		typeof value === "string" ||
		typeof value === "number" ||
		typeof value === "boolean"
	) {
		return typeof value === "string" ? redact(value) : value;
	}
	if (value instanceof Error) {
		return redact(value.message);
	}
	return redact(JSON.stringify(value));
}

export function errorMessage(error: unknown): string {
	if (error instanceof Error) {
		return redact(error.message);
	}
	return redact(String(error));
}

export function logStructured(
	level: LogLevel,
	message: string,
	fields: Record<string, unknown> = {},
): void {
	const entry: Record<string, string | number | boolean | null> = {
		timestamp: new Date().toISOString(),
		level,
		message,
	};
	for (const [key, value] of Object.entries(fields)) {
		entry[key] = toLogValue(value);
	}
	process.stderr.write(`${JSON.stringify(entry)}\n`);
}
