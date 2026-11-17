"""YAML loading that remembers line numbers, so evidence can point to file:line."""
from __future__ import annotations

import yaml

LINE = "__line__"
VALUE_LINES = "__value_lines__"


class _LineLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node, deep=False):
    mapping = yaml.SafeLoader.construct_mapping(loader, node, deep=deep)
    mapping[LINE] = node.start_mark.line + 1
    # Line where each value starts (block scalars start on the line after the key).
    vlines = {}
    for k_node, v_node in node.value:
        key = loader.construct_object(k_node, deep=deep)
        start = v_node.start_mark.line + 1
        if isinstance(v_node, yaml.ScalarNode) and v_node.style in ("|", ">"):
            start += 1
        if isinstance(key, str):
            vlines[key] = start
    mapping[VALUE_LINES] = vlines
    return mapping


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load(text: str):
    return yaml.load(text, Loader=_LineLoader)


def load_plain(text: str):
    return yaml.safe_load(text)


def line_of(d: dict, key: str | None = None) -> int | None:
    if not isinstance(d, dict):
        return None
    if key is not None:
        return d.get(VALUE_LINES, {}).get(key) or d.get(LINE)
    return d.get(LINE)


def strip_lines(obj):
    """Copy of obj without the line bookkeeping keys (for hashing/serializing)."""
    if isinstance(obj, dict):
        return {k: strip_lines(v) for k, v in obj.items() if k not in (LINE, VALUE_LINES)}
    if isinstance(obj, list):
        return [strip_lines(v) for v in obj]
    return obj
