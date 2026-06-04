/**
 * Read and validate an integer environment variable.
 *
 * Returns `defaultValue` when the variable is unset or empty; throws when it is
 * set to a value outside the inclusive `[min, max]` range.
 */
export function readIntegerEnv(
	name: string,
	defaultValue: number,
	{ min, max }: { min: number; max: number },
): number {
	const raw = process.env[name];
	if (raw === undefined || raw === "") {
		return defaultValue;
	}
	const parsed = Number.parseInt(raw, 10);
	if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
		throw new Error(`${name} must be an integer between ${min} and ${max}`);
	}
	return parsed;
}
