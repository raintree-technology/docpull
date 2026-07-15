"""Unambiguous serialization helpers for signed benchmark inputs."""

from __future__ import annotations

from typing import Any

import yaml


class _UniqueKeySafeLoader(yaml.SafeLoader):  # type: ignore[misc, no-any-unimported]
    """SafeLoader variant that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ValueError("YAML mapping keys must be hashable") from error
        if duplicate:
            raise ValueError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def strict_yaml_load(payload: str) -> Any:
    """Parse safe YAML while rejecting parser-dependent duplicate keys."""

    return yaml.load(payload, Loader=_UniqueKeySafeLoader)
