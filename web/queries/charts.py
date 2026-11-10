"""Shared Plotly settings for every chart the app renders."""

# Charts fill their container; the modebar is hidden on phones by CSS (input.css).
PLOTLY_CONFIG = {
    "displayModeBar": "hover",
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "displaylogo": False,
    "responsive": True,
}

HOVER_LABEL = dict(bgcolor="#1f2937", font_color="#e5e7eb", bordercolor="#374151")


def wrap_chart(html: str) -> str:
    """Wrap Plotly HTML in a container for modebar positioning."""
    return f'<div class="plotly-chart-wrap">{html}</div>'
