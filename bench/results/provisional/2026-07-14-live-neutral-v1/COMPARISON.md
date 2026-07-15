# live-neutral-extract comparison

Suite version: `1.0.0`  
Suite SHA-256: `02b0ae9e23cb938b27082e9f27e0a40468672bfab6491fa8c80a15efdc4a1786`

Rows are separated by capability lane. Accounted cost can combine provider-reported actual cost and documented upper bounds; the cost-kind columns make that distinction explicit.

A pass requires every declared deterministic check, not merely a non-empty response.

## Overall

| Lane | System | Cases | Trials | Complete | Pass all | Quality | Mean seconds | Accounted USD | Actual / upper / unknown |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| extract | docpull | 32 | 96 | 68.8% | 68.8% | 68.8% | 1.184 | $0.000000 | 96 / 0 / 0 |
| extract | parallel | 32 | 96 | 96.9% | 93.8% | 96.4% | 0.759 | $0.096000 | 0 / 96 / 0 |
| extract | tavily | 32 | 96 | 91.7% | 84.4% | 90.1% | 1.627 | $0.768000 | 0 / 96 / 0 |
| extract | tavily-advanced | 32 | 96 | 96.9% | 93.8% | 96.4% | 0.864 | $1.536000 | 0 / 96 / 0 |

## Split and family slices

| Lane | Slice | System | Cases | Trials | Complete | Pass all | Quality | Mean seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| extract | split:dev | docpull | 16 | 48 | 50.0% | 50.0% | 50.0% | 1.027 |
| extract | split:dev | parallel | 16 | 48 | 100.0% | 93.8% | 99.0% | 0.768 |
| extract | split:dev | tavily | 16 | 48 | 93.8% | 81.2% | 90.6% | 1.517 |
| extract | split:dev | tavily-advanced | 16 | 48 | 93.8% | 87.5% | 92.7% | 0.912 |
| extract | split:test | docpull | 16 | 48 | 87.5% | 87.5% | 87.5% | 1.341 |
| extract | split:test | parallel | 16 | 48 | 93.8% | 93.8% | 93.8% | 0.750 |
| extract | split:test | tavily | 16 | 48 | 89.6% | 87.5% | 89.6% | 1.737 |
| extract | split:test | tavily-advanced | 16 | 48 | 100.0% | 100.0% | 100.0% | 0.816 |
| extract | family:long-form | docpull | 2 | 6 | 50.0% | 50.0% | 50.0% | 0.546 |
| extract | family:long-form | parallel | 2 | 6 | 100.0% | 100.0% | 100.0% | 0.393 |
| extract | family:long-form | tavily | 2 | 6 | 100.0% | 50.0% | 83.3% | 0.851 |
| extract | family:long-form | tavily-advanced | 2 | 6 | 100.0% | 100.0% | 100.0% | 0.602 |
| extract | family:long-reference | docpull | 4 | 12 | 75.0% | 75.0% | 75.0% | 1.953 |
| extract | family:long-reference | parallel | 4 | 12 | 100.0% | 100.0% | 100.0% | 0.657 |
| extract | family:long-reference | tavily | 4 | 12 | 100.0% | 100.0% | 100.0% | 3.716 |
| extract | family:long-reference | tavily-advanced | 4 | 12 | 100.0% | 100.0% | 100.0% | 0.653 |
| extract | family:managed-access | docpull | 1 | 3 | 0.0% | 0.0% | 0.0% | 0.666 |
| extract | family:managed-access | parallel | 1 | 3 | 100.0% | 100.0% | 100.0% | 0.478 |
| extract | family:managed-access | tavily | 1 | 3 | 100.0% | 100.0% | 100.0% | 3.045 |
| extract | family:managed-access | tavily-advanced | 1 | 3 | 100.0% | 100.0% | 100.0% | 3.004 |
| extract | family:modern-web | docpull | 2 | 6 | 100.0% | 100.0% | 100.0% | 0.923 |
| extract | family:modern-web | parallel | 2 | 6 | 100.0% | 100.0% | 100.0% | 0.372 |
| extract | family:modern-web | tavily | 2 | 6 | 100.0% | 100.0% | 100.0% | 0.397 |
| extract | family:modern-web | tavily-advanced | 2 | 6 | 100.0% | 100.0% | 100.0% | 0.402 |
| extract | family:pdf | docpull | 2 | 6 | 0.0% | 0.0% | 0.0% | 0.478 |
| extract | family:pdf | parallel | 2 | 6 | 100.0% | 100.0% | 100.0% | 1.383 |
| extract | family:pdf | tavily | 2 | 6 | 100.0% | 100.0% | 100.0% | 0.498 |
| extract | family:pdf | tavily-advanced | 2 | 6 | 100.0% | 100.0% | 100.0% | 0.488 |
| extract | family:raw-text | docpull | 3 | 9 | 33.3% | 33.3% | 33.3% | 0.621 |
| extract | family:raw-text | parallel | 3 | 9 | 100.0% | 66.7% | 94.4% | 0.335 |
| extract | family:raw-text | tavily | 3 | 9 | 100.0% | 66.7% | 94.4% | 0.792 |
| extract | family:raw-text | tavily-advanced | 3 | 9 | 100.0% | 66.7% | 94.4% | 0.514 |
| extract | family:standards | docpull | 7 | 21 | 57.1% | 57.1% | 57.1% | 1.480 |
| extract | family:standards | parallel | 7 | 21 | 100.0% | 100.0% | 100.0% | 1.317 |
| extract | family:standards | tavily | 7 | 21 | 85.7% | 85.7% | 85.7% | 2.536 |
| extract | family:standards | tavily-advanced | 7 | 21 | 85.7% | 85.7% | 85.7% | 1.792 |
| extract | family:technical-docs | docpull | 11 | 33 | 100.0% | 100.0% | 100.0% | 1.209 |
| extract | family:technical-docs | parallel | 11 | 33 | 90.9% | 90.9% | 90.9% | 0.605 |
| extract | family:technical-docs | tavily | 11 | 33 | 84.8% | 81.8% | 84.8% | 0.957 |
| extract | family:technical-docs | tavily-advanced | 11 | 33 | 100.0% | 100.0% | 100.0% | 0.452 |

## Per-case results

| Case | Split | Family | System | Complete | Passed | Quality | Mean seconds | Accounted USD |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `dev.access.pypi-pydantic` | dev | managed-access | docpull | 0/3 | 0/3 | 0.0% | 0.666 | $0.000000 |
| `dev.access.pypi-pydantic` | dev | managed-access | parallel | 3/3 | 3/3 | 100.0% | 0.478 | $0.003000 |
| `dev.access.pypi-pydantic` | dev | managed-access | tavily | 3/3 | 3/3 | 100.0% | 3.045 | $0.024000 |
| `dev.access.pypi-pydantic` | dev | managed-access | tavily-advanced | 3/3 | 3/3 | 100.0% | 3.004 | $0.048000 |
| `dev.docs.git-rebase` | dev | long-reference | docpull | 3/3 | 3/3 | 100.0% | 1.193 | $0.000000 |
| `dev.docs.git-rebase` | dev | long-reference | parallel | 3/3 | 3/3 | 100.0% | 0.444 | $0.003000 |
| `dev.docs.git-rebase` | dev | long-reference | tavily | 3/3 | 3/3 | 100.0% | 0.537 | $0.024000 |
| `dev.docs.git-rebase` | dev | long-reference | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.537 | $0.048000 |
| `dev.docs.kubernetes-object-names` | dev | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 1.124 | $0.000000 |
| `dev.docs.kubernetes-object-names` | dev | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 0.373 | $0.003000 |
| `dev.docs.kubernetes-object-names` | dev | technical-docs | tavily | 3/3 | 3/3 | 100.0% | 0.417 | $0.024000 |
| `dev.docs.kubernetes-object-names` | dev | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.371 | $0.048000 |
| `dev.docs.mdn-using-fetch` | dev | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 1.742 | $0.000000 |
| `dev.docs.mdn-using-fetch` | dev | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 0.385 | $0.003000 |
| `dev.docs.mdn-using-fetch` | dev | technical-docs | tavily | 3/3 | 3/3 | 100.0% | 0.782 | $0.024000 |
| `dev.docs.mdn-using-fetch` | dev | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.435 | $0.048000 |
| `dev.docs.python-control-flow` | dev | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 0.775 | $0.000000 |
| `dev.docs.python-control-flow` | dev | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 0.379 | $0.003000 |
| `dev.docs.python-control-flow` | dev | technical-docs | tavily | 3/3 | 3/3 | 100.0% | 2.530 | $0.024000 |
| `dev.docs.python-control-flow` | dev | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.568 | $0.048000 |
| `dev.docs.sqlite-select` | dev | long-reference | docpull | 3/3 | 3/3 | 100.0% | 3.406 | $0.000000 |
| `dev.docs.sqlite-select` | dev | long-reference | parallel | 3/3 | 3/3 | 100.0% | 0.777 | $0.003000 |
| `dev.docs.sqlite-select` | dev | long-reference | tavily | 3/3 | 3/3 | 100.0% | 0.528 | $0.024000 |
| `dev.docs.sqlite-select` | dev | long-reference | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.520 | $0.048000 |
| `dev.long.wikipedia-grace-hopper` | dev | long-form | docpull | 0/3 | 0/3 | 0.0% | 0.411 | $0.000000 |
| `dev.long.wikipedia-grace-hopper` | dev | long-form | parallel | 3/3 | 3/3 | 100.0% | 0.487 | $0.003000 |
| `dev.long.wikipedia-grace-hopper` | dev | long-form | tavily | 3/3 | 3/3 | 100.0% | 0.604 | $0.024000 |
| `dev.long.wikipedia-grace-hopper` | dev | long-form | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.646 | $0.048000 |
| `dev.modern.nextjs-installation` | dev | modern-web | docpull | 3/3 | 3/3 | 100.0% | 1.096 | $0.000000 |
| `dev.modern.nextjs-installation` | dev | modern-web | parallel | 3/3 | 3/3 | 100.0% | 0.319 | $0.003000 |
| `dev.modern.nextjs-installation` | dev | modern-web | tavily | 3/3 | 3/3 | 100.0% | 0.426 | $0.024000 |
| `dev.modern.nextjs-installation` | dev | modern-web | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.403 | $0.048000 |
| `dev.pdf.attention-paper` | dev | pdf | docpull | 0/3 | 0/3 | 0.0% | 0.506 | $0.000000 |
| `dev.pdf.attention-paper` | dev | pdf | parallel | 3/3 | 3/3 | 100.0% | 0.689 | $0.003000 |
| `dev.pdf.attention-paper` | dev | pdf | tavily | 3/3 | 3/3 | 100.0% | 0.446 | $0.024000 |
| `dev.pdf.attention-paper` | dev | pdf | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.451 | $0.048000 |
| `dev.raw.cpython-readme` | dev | raw-text | docpull | 0/3 | 0/3 | 0.0% | 0.669 | $0.000000 |
| `dev.raw.cpython-readme` | dev | raw-text | parallel | 3/3 | 3/3 | 100.0% | 0.366 | $0.003000 |
| `dev.raw.cpython-readme` | dev | raw-text | tavily | 3/3 | 3/3 | 100.0% | 1.025 | $0.024000 |
| `dev.raw.cpython-readme` | dev | raw-text | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.362 | $0.048000 |
| `dev.raw.kubernetes-readme` | dev | raw-text | docpull | 3/3 | 3/3 | 100.0% | 0.595 | $0.000000 |
| `dev.raw.kubernetes-readme` | dev | raw-text | parallel | 3/3 | 3/3 | 100.0% | 0.307 | $0.003000 |
| `dev.raw.kubernetes-readme` | dev | raw-text | tavily | 3/3 | 3/3 | 100.0% | 0.376 | $0.024000 |
| `dev.raw.kubernetes-readme` | dev | raw-text | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.350 | $0.048000 |
| `dev.raw.openapi-petstore` | dev | raw-text | docpull | 0/3 | 0/3 | 0.0% | 0.600 | $0.000000 |
| `dev.raw.openapi-petstore` | dev | raw-text | parallel | 3/3 | 0/3 | 83.3% | 0.332 | $0.003000 |
| `dev.raw.openapi-petstore` | dev | raw-text | tavily | 3/3 | 0/3 | 83.3% | 0.974 | $0.024000 |
| `dev.raw.openapi-petstore` | dev | raw-text | tavily-advanced | 3/3 | 0/3 | 83.3% | 0.829 | $0.048000 |
| `dev.raw.rfc8259-text` | dev | standards | docpull | 0/3 | 0/3 | 0.0% | 0.813 | $0.000000 |
| `dev.raw.rfc8259-text` | dev | standards | parallel | 3/3 | 3/3 | 100.0% | 1.460 | $0.003000 |
| `dev.raw.rfc8259-text` | dev | standards | tavily | 3/3 | 3/3 | 100.0% | 0.463 | $0.024000 |
| `dev.raw.rfc8259-text` | dev | standards | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.438 | $0.048000 |
| `dev.research.attention-abstract` | dev | long-form | docpull | 3/3 | 3/3 | 100.0% | 0.681 | $0.000000 |
| `dev.research.attention-abstract` | dev | long-form | parallel | 3/3 | 3/3 | 100.0% | 0.300 | $0.003000 |
| `dev.research.attention-abstract` | dev | long-form | tavily | 3/3 | 0/3 | 66.7% | 1.099 | $0.024000 |
| `dev.research.attention-abstract` | dev | long-form | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.558 | $0.048000 |
| `dev.standard.rfc9110-html` | dev | standards | docpull | 0/3 | 0/3 | 0.0% | 1.725 | $0.000000 |
| `dev.standard.rfc9110-html` | dev | standards | parallel | 3/3 | 3/3 | 100.0% | 4.540 | $0.003000 |
| `dev.standard.rfc9110-html` | dev | standards | tavily | 0/3 | 0/3 | 0.0% | 10.438 | $0.024000 |
| `dev.standard.rfc9110-html` | dev | standards | tavily-advanced | 0/3 | 0/3 | 0.0% | 4.531 | $0.048000 |
| `dev.standard.wcag-22` | dev | standards | docpull | 0/3 | 0/3 | 0.0% | 0.429 | $0.000000 |
| `dev.standard.wcag-22` | dev | standards | parallel | 3/3 | 3/3 | 100.0% | 0.655 | $0.003000 |
| `dev.standard.wcag-22` | dev | standards | tavily | 3/3 | 3/3 | 100.0% | 0.581 | $0.024000 |
| `dev.standard.wcag-22` | dev | standards | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.591 | $0.048000 |
| `test.docs.curl-manpage` | test | long-reference | docpull | 3/3 | 3/3 | 100.0% | 2.769 | $0.000000 |
| `test.docs.curl-manpage` | test | long-reference | parallel | 3/3 | 3/3 | 100.0% | 0.708 | $0.003000 |
| `test.docs.curl-manpage` | test | long-reference | tavily | 3/3 | 3/3 | 100.0% | 0.749 | $0.024000 |
| `test.docs.curl-manpage` | test | long-reference | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.683 | $0.048000 |
| `test.docs.django-tutorial` | test | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 0.933 | $0.000000 |
| `test.docs.django-tutorial` | test | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 0.450 | $0.003000 |
| `test.docs.django-tutorial` | test | technical-docs | tavily | 3/3 | 3/3 | 100.0% | 1.235 | $0.024000 |
| `test.docs.django-tutorial` | test | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.497 | $0.048000 |
| `test.docs.docker-overview` | test | modern-web | docpull | 3/3 | 3/3 | 100.0% | 0.750 | $0.000000 |
| `test.docs.docker-overview` | test | modern-web | parallel | 3/3 | 3/3 | 100.0% | 0.426 | $0.003000 |
| `test.docs.docker-overview` | test | modern-web | tavily | 3/3 | 3/3 | 100.0% | 0.368 | $0.024000 |
| `test.docs.docker-overview` | test | modern-web | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.401 | $0.048000 |
| `test.docs.fastapi-first-steps` | test | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 0.797 | $0.000000 |
| `test.docs.fastapi-first-steps` | test | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 0.394 | $0.003000 |
| `test.docs.fastapi-first-steps` | test | technical-docs | tavily | 3/3 | 3/3 | 100.0% | 0.458 | $0.024000 |
| `test.docs.fastapi-first-steps` | test | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.466 | $0.048000 |
| `test.docs.go-spec` | test | standards | docpull | 3/3 | 3/3 | 100.0% | 1.523 | $0.000000 |
| `test.docs.go-spec` | test | standards | parallel | 3/3 | 3/3 | 100.0% | 0.794 | $0.003000 |
| `test.docs.go-spec` | test | standards | tavily | 3/3 | 3/3 | 100.0% | 0.684 | $0.024000 |
| `test.docs.go-spec` | test | standards | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.692 | $0.048000 |
| `test.docs.nginx-beginners` | test | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 1.133 | $0.000000 |
| `test.docs.nginx-beginners` | test | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 0.380 | $0.003000 |
| `test.docs.nginx-beginners` | test | technical-docs | tavily | 3/3 | 3/3 | 100.0% | 0.446 | $0.024000 |
| `test.docs.nginx-beginners` | test | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.430 | $0.048000 |
| `test.docs.node-filesystem` | test | long-reference | docpull | 0/3 | 0/3 | 0.0% | 0.445 | $0.000000 |
| `test.docs.node-filesystem` | test | long-reference | parallel | 3/3 | 3/3 | 100.0% | 0.702 | $0.003000 |
| `test.docs.node-filesystem` | test | long-reference | tavily | 3/3 | 3/3 | 100.0% | 13.050 | $0.024000 |
| `test.docs.node-filesystem` | test | long-reference | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.872 | $0.048000 |
| `test.docs.numpy-beginners` | test | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 0.914 | $0.000000 |
| `test.docs.numpy-beginners` | test | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 0.442 | $0.003000 |
| `test.docs.numpy-beginners` | test | technical-docs | tavily | 3/3 | 3/3 | 100.0% | 0.543 | $0.024000 |
| `test.docs.numpy-beginners` | test | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.538 | $0.048000 |
| `test.docs.postgresql-tutorial` | test | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 2.379 | $0.000000 |
| `test.docs.postgresql-tutorial` | test | technical-docs | parallel | 0/3 | 0/3 | 0.0% | 1.347 | $0.003000 |
| `test.docs.postgresql-tutorial` | test | technical-docs | tavily | 1/3 | 1/3 | 33.3% | 1.883 | $0.024000 |
| `test.docs.postgresql-tutorial` | test | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.484 | $0.048000 |
| `test.docs.ruff-tutorial` | test | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 0.782 | $0.000000 |
| `test.docs.ruff-tutorial` | test | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 0.498 | $0.003000 |
| `test.docs.ruff-tutorial` | test | technical-docs | tavily | 3/3 | 3/3 | 100.0% | 0.396 | $0.024000 |
| `test.docs.ruff-tutorial` | test | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.369 | $0.048000 |
| `test.docs.rust-ownership` | test | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 0.600 | $0.000000 |
| `test.docs.rust-ownership` | test | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 0.537 | $0.003000 |
| `test.docs.rust-ownership` | test | technical-docs | tavily | 3/3 | 3/3 | 100.0% | 0.482 | $0.024000 |
| `test.docs.rust-ownership` | test | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.431 | $0.048000 |
| `test.docs.serde` | test | technical-docs | docpull | 3/3 | 3/3 | 100.0% | 2.119 | $0.000000 |
| `test.docs.serde` | test | technical-docs | parallel | 3/3 | 3/3 | 100.0% | 1.468 | $0.003000 |
| `test.docs.serde` | test | technical-docs | tavily | 0/3 | 0/3 | 0.0% | 1.353 | $0.024000 |
| `test.docs.serde` | test | technical-docs | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.379 | $0.048000 |
| `test.pdf.ray-paper` | test | pdf | docpull | 0/3 | 0/3 | 0.0% | 0.449 | $0.000000 |
| `test.pdf.ray-paper` | test | pdf | parallel | 3/3 | 3/3 | 100.0% | 2.077 | $0.003000 |
| `test.pdf.ray-paper` | test | pdf | tavily | 3/3 | 3/3 | 100.0% | 0.550 | $0.024000 |
| `test.pdf.ray-paper` | test | pdf | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.526 | $0.048000 |
| `test.standard.ecmascript-lexical` | test | standards | docpull | 3/3 | 3/3 | 100.0% | 2.452 | $0.000000 |
| `test.standard.ecmascript-lexical` | test | standards | parallel | 3/3 | 3/3 | 100.0% | 0.615 | $0.003000 |
| `test.standard.ecmascript-lexical` | test | standards | tavily | 3/3 | 3/3 | 100.0% | 4.362 | $0.024000 |
| `test.standard.ecmascript-lexical` | test | standards | tavily-advanced | 3/3 | 3/3 | 100.0% | 4.990 | $0.048000 |
| `test.standard.json-schema-core` | test | standards | docpull | 3/3 | 3/3 | 100.0% | 1.227 | $0.000000 |
| `test.standard.json-schema-core` | test | standards | parallel | 3/3 | 3/3 | 100.0% | 0.536 | $0.003000 |
| `test.standard.json-schema-core` | test | standards | tavily | 3/3 | 3/3 | 100.0% | 0.608 | $0.024000 |
| `test.standard.json-schema-core` | test | standards | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.617 | $0.048000 |
| `test.standard.openapi-310` | test | standards | docpull | 3/3 | 3/3 | 100.0% | 2.189 | $0.000000 |
| `test.standard.openapi-310` | test | standards | parallel | 3/3 | 3/3 | 100.0% | 0.621 | $0.003000 |
| `test.standard.openapi-310` | test | standards | tavily | 3/3 | 3/3 | 100.0% | 0.617 | $0.024000 |
| `test.standard.openapi-310` | test | standards | tavily-advanced | 3/3 | 3/3 | 100.0% | 0.685 | $0.048000 |

No cross-lane rank or single winner is computed.
