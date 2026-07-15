from __future__ import annotations

import pytest

from docpull_bench.integrity import strict_json_loads


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_rejects_nonstandard_numeric_constants(constant: str) -> None:
    with pytest.raises(ValueError, match="non-standard JSON constant"):
        strict_json_loads(f'{{"value":{constant}}}')
