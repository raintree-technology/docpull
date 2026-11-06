# docpull 6.9

Repeat crawls of sitemap sites now send far fewer requests.

- With `--cache`, a page whose sitemap `lastmod` has not changed is skipped without a request, as long as its Markdown file still exists.
- `--max-age DAYS` (default 14) revalidates those pages over HTTP on a schedule, so a page that changes without a new date is still caught.
- Runs report URLs that left the sitemap. `--prune-removed` deletes their local files.
- `--not-modified-delay` shortens the wait after a 304. It never undercuts robots.txt Crawl-delay.

Also fixed: incremental Markdown runs keep skipped pages in `corpus.manifest.json`, a cached run with no changes exits 0, and `docpull refresh` works on Markdown packs.

The first crawl takes as long as before, and sites without `lastmod` still use conditional GET.
