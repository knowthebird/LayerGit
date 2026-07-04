from __future__ import annotations

from .errors import LayerError


def layer_enabled(layer: dict) -> bool:
    return layer.get("enabled", True)


def select_layers(
    layers: list[dict],
    selector: str | None,
    *,
    enabled_only_for_default: bool = False,
) -> list[int]:
    if not layers:
        return []
    if selector in (None, "", "all"):
        indexes = list(range(len(layers)))
        if enabled_only_for_default:
            indexes = [index for index in indexes if layer_enabled(layers[index])]
        return indexes
    if selector == "all-layers":
        return list(range(len(layers)))
    if selector == "enabled":
        return [index for index, layer in enumerate(layers) if layer_enabled(layer)]
    if selector == "disabled":
        return [index for index, layer in enumerate(layers) if not layer_enabled(layer)]
    if selector == "top":
        indexes = [index for index, layer in enumerate(layers) if layer_enabled(layer)]
        if enabled_only_for_default and indexes:
            return [indexes[-1]]
        return [len(layers) - 1]
    if "," in selector:
        indexes: list[int] = []
        for part in selector.split(","):
            indexes.extend(
                select_layers(
                    layers,
                    part.strip(),
                    enabled_only_for_default=enabled_only_for_default,
                )
            )
        return dedupe(indexes)
    if ".." in selector:
        start_raw, end_raw = selector.split("..", 1)
        start = one_based_index(layers, start_raw)
        end = one_based_index(layers, end_raw)
        if start > end:
            raise LayerError(f"Invalid selector range: {selector}")
        return list(range(start, end + 1))
    if selector.isdigit():
        return [one_based_index(layers, selector)]

    matches = [i for i, layer in enumerate(layers) if layer.get("name") == selector]
    if not matches:
        raise LayerError(f"No layer matches selector `{selector}`")
    return matches


def one_based_index(layers: list[dict], raw: str) -> int:
    if not raw.isdigit():
        raise LayerError(f"Expected a layer index, got `{raw}`")
    index = int(raw) - 1
    if index < 0 or index >= len(layers):
        raise LayerError(f"Layer index out of range: {raw}")
    return index


def insertion_index(layers: list[dict], *, before: str | None, after: str | None, top: bool) -> int:
    if top or (before is None and after is None):
        return len(layers)
    if before and after:
        raise LayerError("Use only one of --before or --after")
    if before:
        return select_layers(layers, before)[0]
    if after:
        return select_layers(layers, after)[-1] + 1
    return len(layers)


def dedupe(indexes: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for index in indexes:
        if index not in seen:
            seen.add(index)
            result.append(index)
    return result
