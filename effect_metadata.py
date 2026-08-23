#!/usr/bin/env python3
"""Structured parsing of WLED /json/fxdata effect metadata (v0.14+).

Each fxdata entry is indexed by effect id (same index as /json/eff) and holds
up to 5 semicolon-separated sections, optionally prefixed with the effect
name and "@":

    name@<parameters>;<colors>;<palette>;<flags>;<defaults>

- parameters: comma-separated slider labels for sx, ix, c1, c2, c3 in that
  order. Empty label = slider hidden; "!" = default label (Speed, Intensity,
  Custom 1..3). A missing section falls back to speed+intensity shown.
- colors: comma-separated color-slot labels (up to 3); empty/missing =
  default 3 color slots.
- palette: empty = palette control hidden (effect ignores palettes);
  "!" = shown with default label; missing section = palette enabled.
- flags: "1" 1D-optimized, "2" requires 2D matrix, "3" 3D,
  "v" audio-reactive (volume), "f" audio-reactive (frequency).
- defaults: comma-separated key=value tuned defaults, e.g. "sx=24,pal=50".

fxdata strings vary across WLED versions, so every parser here tolerates
short, empty, or malformed entries and falls back to WLED's defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SLIDER_KEYS = ("sx", "ix", "c1", "c2", "c3")
DEFAULT_SLIDER_LABELS = {
    "sx": "Speed",
    "ix": "Intensity",
    "c1": "Custom 1",
    "c2": "Custom 2",
    "c3": "Custom 3",
}
DEFAULT_COLOR_COUNT = 3
RESERVED_NAMES = {"rsvd", "-"}

_FLAG_CHARS = {"1": "one_d", "2": "two_d", "3": "three_d", "v": "audio_volume", "f": "audio_freq"}


@dataclass
class EffectMeta:
    """Structured metadata for one WLED effect."""

    id: int
    name: str
    sliders: dict[str, str | None] = field(default_factory=dict)  # sx/ix/c1-c3 -> label, None = hidden
    color_labels: list[str] = field(default_factory=list)
    uses_palette: bool = True
    one_d: bool = False
    two_d: bool = False
    three_d: bool = False
    audio_volume: bool = False
    audio_freq: bool = False
    defaults: dict[str, int | str] = field(default_factory=dict)

    @property
    def audio_reactive(self) -> bool:
        return self.audio_volume or self.audio_freq


def _default_sliders() -> dict[str, str | None]:
    return {"sx": "Speed", "ix": "Intensity", "c1": None, "c2": None, "c3": None}


def _default_colors() -> list[str]:
    return [f"Color {i + 1}" for i in range(DEFAULT_COLOR_COUNT)]


def _strip_name_prefix(entry: str) -> tuple[str | None, str]:
    """Split an optional "name@" prefix off an fxdata entry."""
    if "@" in entry and (";" not in entry or entry.index("@") < entry.index(";")):
        name, rest = entry.split("@", 1)
        return name.strip() or None, rest
    return None, entry


def _parse_sliders(section: str | None) -> dict[str, str | None]:
    if section is None:
        return _default_sliders()
    labels = section.split(",")
    sliders: dict[str, str | None] = {}
    for index, key in enumerate(SLIDER_KEYS):
        label = labels[index].strip() if index < len(labels) else ""
        if not label:
            sliders[key] = None
        elif label == "!":
            sliders[key] = DEFAULT_SLIDER_LABELS[key]
        else:
            sliders[key] = label
    return sliders


def _parse_colors(section: str | None) -> list[str]:
    if section is None:
        return _default_colors()
    labels = [label.strip() for label in section.split(",")]
    while labels and not labels[-1]:
        labels.pop()
    if not labels:
        return _default_colors()
    return [
        label if label and label != "!" else f"Color {index + 1}"
        for index, label in enumerate(labels[:3])
    ]


def _parse_palette(sections: list[str]) -> bool:
    if len(sections) < 3:
        return True  # missing section: palette enabled by default
    return bool(sections[2].strip())  # empty = palette control hidden


def _parse_defaults(section: str | None) -> dict[str, int | str]:
    defaults: dict[str, int | str] = {}
    if not section:
        return defaults
    for pair in section.split(","):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        try:
            defaults[key] = int(value)
        except ValueError:
            defaults[key] = value
    return defaults


def parse_fxdata_entry(entry: object, effect_id: int = 0, name: str = "") -> EffectMeta:
    """Parse one raw fxdata string into an EffectMeta (never raises)."""
    meta = EffectMeta(
        id=effect_id,
        name=name,
        sliders=_default_sliders(),
        color_labels=_default_colors(),
    )
    text = entry if isinstance(entry, str) else ""
    prefix_name, rest = _strip_name_prefix(text.strip())
    if not meta.name and prefix_name:
        meta.name = prefix_name
    # Bare effect name or empty entry (no "@"/";"/parameter metadata): all defaults.
    if not rest or (prefix_name is None and not any(mark in rest for mark in (";", ",", "!"))):
        return meta
    sections = rest.split(";")
    meta.sliders = _parse_sliders(sections[0])
    meta.color_labels = _parse_colors(sections[1] if len(sections) > 1 else None)
    meta.uses_palette = _parse_palette(sections)
    if len(sections) > 3:
        for char in sections[3].strip():
            attr = _FLAG_CHARS.get(char)
            if attr:
                setattr(meta, attr, True)
    meta.defaults = _parse_defaults(sections[4] if len(sections) > 4 else None)
    return meta


def parse_fxdata(names: list, fxdata: list | None = None) -> dict[int, EffectMeta]:
    """Build an effect-id -> EffectMeta catalog from /json/eff + /json/fxdata.

    RSVD/"-"/empty/None placeholder slots are filtered out. When the names
    list is short, the effect name is recovered from an fxdata "name@" prefix.
    """
    names = names or []
    fxdata = fxdata or []
    catalog: dict[int, EffectMeta] = {}
    for effect_id in range(max(len(names), len(fxdata))):
        raw_name = names[effect_id] if effect_id < len(names) else None
        name = str(raw_name).strip() if raw_name is not None else ""
        entry = fxdata[effect_id] if effect_id < len(fxdata) else ""
        meta = parse_fxdata_entry(entry, effect_id=effect_id, name=name)
        if not meta.name or meta.name.lower() in RESERVED_NAMES:
            continue
        catalog[effect_id] = meta
    return catalog


def render_effect_line(meta: EffectMeta) -> str:
    """One compact LLM-prompt line, e.g.

    66 Fire 2012 [1D] sliders: sx=Cooling, ix=Spark rate; pal=yes; defaults: pal=35, sx=64
    """
    flags = ""
    if meta.one_d:
        flags += " [1D]"
    if meta.two_d:
        flags += " [2D]"
    if meta.three_d:
        flags += " [3D]"
    if meta.audio_volume:
        flags += " [vol]"
    if meta.audio_freq:
        flags += " [freq]"
    parts = [f"{meta.id} {meta.name}{flags}"]
    custom_sliders = [
        f"{key}={label}"
        for key, label in meta.sliders.items()
        if label and label != DEFAULT_SLIDER_LABELS[key]
    ]
    if custom_sliders:
        parts.append("sliders: " + ", ".join(custom_sliders))
    if meta.color_labels != _default_colors():
        parts.append("colors: " + ", ".join(meta.color_labels))
    if not meta.uses_palette:
        parts.append("pal=no")
    elif len(parts) > 1:
        parts.append("pal=yes")
    if meta.defaults:
        parts.append("defaults: " + ", ".join(f"{key}={value}" for key, value in meta.defaults.items()))
    return "; ".join(parts)


def _is_informative(meta: EffectMeta) -> bool:
    """True when metadata adds something beyond the mood-grouped catalog."""
    return (
        any(label and label != DEFAULT_SLIDER_LABELS[key] for key, label in meta.sliders.items())
        or meta.color_labels != _default_colors()
        or not meta.uses_palette
        or bool(meta.defaults)
    )


def render_catalog_text(names: list, fxdata: list | None = None) -> str:
    """Compact per-effect metadata block for the AI prompt.

    Only effects whose fxdata adds information (custom slider labels, custom
    color slots, hidden palette, or tuned defaults) get a line; audio/2D flags
    are already marked in the mood-grouped catalog. Returns "" when fxdata
    carries nothing extra.
    """
    catalog = parse_fxdata(names, fxdata)
    lines = [render_effect_line(meta) for meta in catalog.values() if _is_informative(meta)]
    if not lines:
        return ""
    header = (
        "Effect metadata from fxdata (custom slider labels for sx/ix/c1-c3; "
        "pal=no = effect ignores palettes; 'defaults:' = WLED tuned values "
        "applied with fxdef):"
    )
    return "\n".join([header, *(f"  {line}" for line in lines)])
