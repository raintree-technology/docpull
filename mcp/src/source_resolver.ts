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

	return {
		ok: true,
		value: {
			name: source,
			url: config.url,
			maxPages: config.maxPages,
		},
	};
}
