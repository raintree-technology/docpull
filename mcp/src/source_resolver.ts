import { Address4, Address6 } from "ip-address";

export interface SourceConfig {
	url: string;
	description: string;
	category: string;
	maxPages?: number;
}

interface ResolvedSource {
	name: string;
	url: string;
	maxPages?: number;
}

type ResolveSourceResult =
	| { ok: true; value: ResolvedSource }
	| { ok: false; message: string };

const URL_SCHEME_RE = /^[a-z][a-z0-9+.-]*:\/\//i;
const SOURCE_NAME_RE = /^[a-zA-Z0-9_.-]+$/;
const MAX_SOURCE_NAME_LENGTH = 128;
const MAX_SOURCE_PAGES = 100_000;
const BLOCKED_HOST_SUFFIXES = [".localhost", ".local", ".internal", ".lan"];

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isSafeSourceName(source: string): boolean {
	return (
		source.length > 0 &&
		source.length <= MAX_SOURCE_NAME_LENGTH &&
		!source.startsWith(".") &&
		source !== ".." &&
		SOURCE_NAME_RE.test(source)
	);
}

function isHttpsUrl(value: string): boolean {
	try {
		const parsed = new URL(value);
		return (
			parsed.protocol === "https:" &&
			parsed.hostname.length > 0 &&
			!isBlockedHost(parsed.hostname)
		);
	} catch (_error) {
		return false;
	}
}

function isBlockedIpv4(host: string): boolean {
	if (!Address4.isValid(host)) {
		return false;
	}
	const address = new Address4(host);
	return (
		address.isPrivate() ||
		address.isLoopback() ||
		address.isLinkLocal() ||
		address.isUnspecified() ||
		address.isBroadcast() ||
		address.isCGNAT() ||
		address.isMulticast()
	);
}

function isBlockedIpv6(host: string): boolean {
	if (!Address6.isValid(host)) {
		return false;
	}
	const address = new Address6(host);
	if (address.isMapped4()) {
		return isBlockedIpv4(address.to4().correctForm());
	}
	return (
		address.isLoopback() ||
		address.isLinkLocal() ||
		address.isULA() ||
		address.isUnspecified() ||
		address.isMulticast()
	);
}

function isBlockedHost(hostname: string): boolean {
	const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
	return (
		host === "localhost" ||
		BLOCKED_HOST_SUFFIXES.some((suffix) => host.endsWith(suffix)) ||
		isBlockedIpv4(host) ||
		isBlockedIpv6(host)
	);
}

function coerceMaxPages(value: unknown, source: string): number | undefined {
	if (value === undefined || value === null) {
		return undefined;
	}
	if (typeof value !== "number" || !Number.isInteger(value)) {
		throw new Error(`source '${source}' maxPages must be an integer`);
	}
	if (value < 1 || value > MAX_SOURCE_PAGES) {
		throw new Error(
			`source '${source}' maxPages must be between 1 and ${MAX_SOURCE_PAGES}`,
		);
	}
	return value;
}

export function normalizeSourceConfig(
	source: string,
	value: unknown,
): { ok: true; config: SourceConfig } | { ok: false; message: string } {
	if (!isSafeSourceName(source)) {
		return {
			ok: false,
			message: `Invalid source name '${source}'. Use alnum plus _, ., or - with no leading dot.`,
		};
	}
	if (!isRecord(value)) {
		return { ok: false, message: `Source '${source}' must be a mapping.` };
	}
	const url = value.url;
	if (typeof url !== "string" || !isHttpsUrl(url)) {
		return {
			ok: false,
			message: `Source '${source}' url must use HTTPS and a public host.`,
		};
	}
	try {
		return {
			ok: true,
			config: {
				url,
				description:
					typeof value.description === "string" ? value.description : "",
				category: typeof value.category === "string" ? value.category : "user",
				maxPages: coerceMaxPages(value.maxPages ?? value.max_pages, source),
			},
		};
	} catch (error) {
		return {
			ok: false,
			message: error instanceof Error ? error.message : String(error),
		};
	}
}

export function resolveConfiguredSource(
	source: string,
	sources: Record<string, SourceConfig>,
	configPath: string,
): ResolveSourceResult {
	if (URL_SCHEME_RE.test(source)) {
		return {
			ok: false,
			message:
				"Direct URLs are disabled for ensure_docs. Add an alias in " +
				configPath +
				" and call ensure_docs with that source name.",
		};
	}

	if (!isSafeSourceName(source)) {
		return {
			ok: false,
			message:
				"Invalid source name: " +
				source +
				". Use alnum plus _, ., or - with no leading dot.",
		};
	}

	const config = sources[source];
	if (!config) {
		return {
			ok: false,
			message:
				"Unknown source: " +
				source +
				". Use list_sources or add it to " +
				configPath +
				".",
		};
	}

	const normalized = normalizeSourceConfig(source, config);
	if (!normalized.ok) {
		return {
			ok: false,
			message: normalized.message,
		};
	}

	return {
		ok: true,
		value: {
			name: source,
			url: normalized.config.url,
			maxPages: normalized.config.maxPages,
		},
	};
}
