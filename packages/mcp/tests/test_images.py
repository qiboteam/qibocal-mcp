"""Data-only report images and the text replacing their decorations."""

import unittest

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from qibocal_mcp.images import colour_name, size, strip


class ImageTests(unittest.TestCase):
    def test_decorations_become_text(self):
        figure = go.Figure([
            go.Scatter(x=[0.0, 0.5], y=[0.4, 0.6], mode="markers", name="Probability",
                       marker_color="#636efa"),
            go.Scatter(x=[0.0, 0.5], y=[0.45, 0.55], mode="lines", name="Fit",
                       line_color="rgb(255, 130, 67)"),
            go.Scatter(x=[0.0, 0.5], y=[0.3, 0.7], fill="toself", name="Errors",
                       fillcolor="rgba(0,100,80,0.2)", line_color="rgba(255,255,255,0)"),
        ])
        figure.update_layout(title="Rabi", xaxis_title="Amplitude [a.u.]",
                             yaxis_title="Excited state probability")
        bare, text = strip(figure)
        self.assertEqual(text, (
            "x Amplitude [a.u.], -0.025 to 0.525; y Excited state probability, 0.28 to 0.72. "
            "Probability (blue markers), Fit (orange line), Errors (teal band)."
        ))
        # The frame spans exactly the described ranges; nothing else is drawn.
        self.assertEqual(list(bare.layout.xaxis.range), [-0.025, 0.525])
        self.assertFalse(bare.layout.showlegend)
        self.assertFalse(bare.layout.xaxis.showticklabels)
        self.assertIsNone(bare.layout.title.text)
        self.assertIsNone(bare.layout.xaxis.title.text)
        # The figure itself is left untouched, for the report.
        self.assertEqual(figure.layout.title.text, "Rabi")

    def test_panels_surfaces_histograms_and_lines(self):
        figure = make_subplots(rows=1, cols=2, subplot_titles=["A", "B"])
        figure.add_trace(go.Heatmap(z=np.arange(6).reshape(2, 3), x=[1, 2, 3], y=[0, 1]), 1, 1)
        figure.add_trace(go.Histogram(x=[1.0, 2.0, 2.0, 3.0], name="counts"), 1, 2)
        figure.add_vline(x=2.0, row=1, col=2)
        figure.update_xaxes(title_text="Click to enter X axis title", row=1, col=2)
        bare, text = strip(figure)
        self.assertEqual(text, (
            "panel 1: x x, 0.9 to 3.1; y y, -0.05 to 1.05. data (heatmap), z 0 to 5. "
            "panel 2: x x, 0.9 to 3.1; y y. counts (red histogram). "
            "Also: vertical line at x 2."
        ))
        # Histogram counts keep an automatic axis; subplot titles are dropped.
        self.assertIsNone(bare.layout.yaxis2.range)
        self.assertEqual(bare.layout.annotations, ())
        self.assertFalse(bare.data[0].showscale)

    def test_colour_names_and_sizes(self):
        for colour, name in (("#00cc96", "green"), ("#EF553B", "red"), ("rgba(0,0,0,0)", None),
                             ("black", "black"), (None, None)):
            with self.subTest(colour=colour):
                self.assertEqual(colour_name(colour), name)
        self.assertEqual(size("data", None), (512, 301))
        self.assertEqual(size("full", None), (1200, 704))
        self.assertEqual(size("data", 1000), (1000, 586))


if __name__ == "__main__":
    unittest.main()
