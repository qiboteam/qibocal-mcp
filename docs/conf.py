"""Sphinx configuration; documentation builds do not import hardware code."""

project = "Qibocal MCP"
author = "Qibocal MCP contributors"
extensions = []
root_doc = "index"
exclude_patterns = ["_build", ".DS_Store", "Thumbs.db"]
html_theme = "alabaster"
html_title = "Qibocal MCP documentation"
html_static_path = []
html_sidebars = {"**": ["about.html", "navigation.html", "searchbox.html"]}
html_theme_options = {"description": "A stdio calibration bridge and agent"}
