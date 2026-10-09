"""Sphinx configuration; documentation builds do not import hardware code."""

project = "qibocal-agent"
author = "qibocal-agent contributors"
extensions = []
root_doc = "index"
exclude_patterns = ["_build", ".DS_Store", "Thumbs.db"]
html_theme = "alabaster"
html_title = "qibocal-agent documentation"
html_static_path = []
html_sidebars = {"**": ["about.html", "navigation.html", "searchbox.html"]}
html_theme_options = {"description": "An autocalibration agent with an MCP server"}
