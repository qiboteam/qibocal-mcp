"""Report images for models: the data alone, with its decorations as text.

Titles, axis labels and legends are fixed per protocol, and tick labels follow
from the parameters: in pixels they cost the model tokens for little
information. By default, images keep only the data, inside a thin frame
spanning the axis ranges, and a short text gives what was stripped: per panel,
axis titles and ranges, and the traces with their colours and kinds. The
decorated figure stays available, as image_style="full", and in the report.

Each run's images are rendered in a single Kaleido call: Kaleido starts a
browser per call, which otherwise dominates the export time.
"""

import math
import re

import numpy as np
import plotly.graph_objects as go
import plotly.io as pio

# Width over height of every rendered image.
ASPECT = 0.586
SIZES = {"data": 512, "full": 1200}
# Fraction of the data span added on each side, as Plotly does by default.
PADDING = 0.05
PLACEHOLDER = "Click to enter"
SURFACES = ("heatmap", "contour", "histogram2d")
COLOURS = {
    "blue": (99, 110, 250), "red": (239, 85, 59), "green": (0, 204, 150),
    "purple": (171, 99, 250), "orange": (255, 161, 90), "cyan": (25, 211, 243),
    "pink": (255, 102, 146), "light green": (182, 232, 128), "magenta": (255, 151, 255),
    "yellow": (254, 203, 82), "black": (0, 0, 0), "grey": (128, 128, 128),
    "white": (255, 255, 255), "brown": (140, 86, 75), "navy": (31, 60, 136),
    "teal": (0, 128, 128),
}


def colour_name(colour) -> str | None:
    """The nearest plain name of a Plotly colour, or None if unreadable."""
    if not isinstance(colour, str):
        return None
    colour = colour.strip().lower()
    if colour.startswith("#") and len(colour) in (4, 7):
        digits = colour[1:]
        if len(digits) == 3:
            digits = "".join(2 * digit for digit in digits)
        rgb = tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
    elif match := re.match(r"rgba?\(([^)]+)\)", colour):
        parts = [float(part) for part in match.group(1).split(",")]
        if len(parts) == 4 and parts[3] == 0:
            return None
        # The hue, whatever its transparency.
        rgb = tuple(parts[:3])
    else:
        return colour if colour.isalpha() else None
    return min(COLOURS, key=lambda name: sum((a - b) ** 2 for a, b in zip(COLOURS[name], rgb)))


def numbers(values) -> np.ndarray | None:
    """Finite numeric values, or None when the data is not numeric."""
    if values is None:
        return None
    try:
        array = np.asarray(values, dtype=float).ravel()
    except (TypeError, ValueError):
        return None
    array = array[np.isfinite(array)]
    return array if array.size else None


def kind(trace) -> str:
    if trace.type in ("heatmap", "contour", "histogram", "histogram2d", "bar", "box"):
        return {"histogram2d": "2D histogram", "bar": "bars"}.get(trace.type, trace.type)
    fill = getattr(trace, "fill", None)
    if fill not in (None, "none"):
        return "band"
    mode = getattr(trace, "mode", None) or "lines+markers"
    return {"markers": "markers", "lines": "line"}.get(mode, mode.replace("+", " and "))


def trace_colour(trace, index: int, colorway) -> str | None:
    if trace.type in SURFACES:
        # Coloured by z, which the description gives instead.
        return None
    if kind(trace) == "band":
        return colour_name(getattr(trace, "fillcolor", None))
    for attribute in ("marker", "line"):
        part = getattr(trace, attribute, None)
        name = colour_name(getattr(part, "color", None)) if part is not None else None
        if name is not None:
            return name
    return colour_name(colorway[index % len(colorway)]) if colorway else None


def axis_key(reference: str | None, letter: str) -> str:
    """Layout key of a trace's axis reference, e.g. "x2" -> "xaxis2"."""
    suffix = (reference or letter)[1:]
    return f"{letter}axis{suffix}"


# Histogram counts are only known after binning: their axis stays automatic.
COUNTS = object()


def axis_values(trace, letter: str):
    if trace.type == "histogram":
        binned = "y" if trace.orientation == "h" else "x"
        return numbers(getattr(trace, letter)) if letter == binned else COUNTS
    values = numbers(getattr(trace, letter, None))
    other = getattr(trace, "y" if letter == "x" else "x", None)
    if values is None and other is not None and trace.type in ("scatter", "bar"):
        # Plotly then places points at their indices.
        return np.arange(len(other), dtype=float)
    return values


def span(values: list[np.ndarray], log: bool) -> list[float] | None:
    data = [array for array in values if array is not None]
    if not data:
        return None
    joined = np.concatenate(data)
    if log:
        joined = np.log10(joined[joined > 0])
        if not joined.size:
            return None
    low, high = float(joined.min()), float(joined.max())
    pad = (high - low) * PADDING or (abs(low) * PADDING or 1.0)
    return [low - pad, high + pad]


def number(value: float) -> str:
    return f"{value:.4g}"


def title(axis) -> str | None:
    text = getattr(getattr(axis, "title", None), "text", None)
    if not text or text.startswith(PLACEHOLDER):
        return None
    return re.sub(r"<[^>]+>", "", text).strip() or None


def strip(figure: go.Figure) -> tuple[go.Figure, str]:
    """The data-only figure, and the text replacing its decorations."""
    bare = go.Figure(figure)
    colorway = list(figure.layout.colorway or figure.layout.template.layout.colorway or [])
    panels: dict[tuple[str, str], list] = {}
    for index, trace in enumerate(bare.data):
        panel = (axis_key(getattr(trace, "xaxis", None), "x"), axis_key(getattr(trace, "yaxis", None), "y"))
        panels.setdefault(panel, []).append((index, trace))
    described = []
    for position, ((xkey, ykey), traces) in enumerate(panels.items(), start=1):
        axes = []
        for key, letter in ((xkey, "x"), (ykey, "y")):
            axis = bare.layout[key]
            log = axis.type == "log"
            values = [axis_values(trace, letter) for _, trace in traces]
            limits = None if any(value is COUNTS for value in values) else span(values, log)
            if limits is not None and axis.range is None:
                bare.layout[key].range = limits
            shown = axis.range if axis.range is not None else limits
            label = title(axis) or letter
            if shown is not None:
                low, high = (10 ** v for v in shown) if log else shown
                axes.append(f"{letter} {label}, {number(low)} to {number(high)}{' (log)' if log else ''}")
            else:
                axes.append(f"{letter} {label}")
        entries, seen = [], set()
        for index, trace in traces:
            colour = trace_colour(trace, index, colorway)
            entry = f"{trace.name or 'data'} ({' '.join(filter(None, (colour, kind(trace))))})"
            if trace.type in SURFACES:
                z = numbers(getattr(trace, "z", None))
                if z is not None:
                    entry += f", z {number(float(z.min()))} to {number(float(z.max()))}"
            if entry not in seen:
                seen.add(entry)
                entries.append(entry)
        prefix = f"panel {position}: " if len(panels) > 1 else ""
        described.append(prefix + "; ".join(axes) + ". " + ", ".join(entries) + ".")
    lines = []
    for shape in bare.layout.shapes or ():
        if shape.type == "line" and shape.x0 == shape.x1 and isinstance(shape.x0, int | float):
            lines.append(f"vertical line at x {number(shape.x0)}")
        elif shape.type == "line" and shape.y0 == shape.y1 and isinstance(shape.y0, int | float):
            lines.append(f"horizontal line at y {number(shape.y0)}")
    if lines:
        described.append("Also: " + ", ".join(lines[:5]) + ".")
    bare.update_layout(
        showlegend=False, title=None, margin=dict(l=2, r=2, t=2, b=2),
        paper_bgcolor="white", plot_bgcolor="white",
    )
    # Panel titles; an update would merge, not clear them.
    bare.layout.annotations = ()
    frame = dict(
        showticklabels=False, title=None, showgrid=False, zeroline=False,
        showline=True, linecolor="#999", mirror=True, ticks="",
    )
    bare.update_xaxes(**frame)
    bare.update_yaxes(**frame)
    bare.for_each_trace(lambda trace: trace.update(showscale=False) if "showscale" in trace else None)
    if "coloraxis" in bare.layout:
        bare.update_coloraxes(showscale=False)
    return bare, " ".join(described)


def size(style: str, width: int | None) -> tuple[int, int]:
    width = SIZES[style] if width is None else width
    return width, math.ceil(width * ASPECT)


def render(figures: list[go.Figure], paths: list, width: int, height: int) -> None:
    """Export all images at once: one browser for the whole run."""
    if figures:
        pio.write_images(figures, [str(path) for path in paths], width=width, height=height)
