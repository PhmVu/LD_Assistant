from fastapi import APIRouter
import plotly.graph_objects as go

router = APIRouter(prefix="/api/ld/dashboard", tags=["dashboard"])


@router.get("/chart")
async def get_dashboard_chart():
    weeks = ["Week 1", "Week 2", "Week 3", "Week 4"]
    passed = [210, 320, 450, 600]
    returned = [40, 35, 25, 20]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=weeks,
            y=passed,
            name="Passed",
            marker_color="#22c55e",
            opacity=0.9,
            hovertemplate="%{x}<br>Passed: %{y}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=weeks,
            y=returned,
            name="Returned",
            marker_color="#fb7185",
            opacity=0.9,
            hovertemplate="%{x}<br>Returned: %{y}<extra></extra>",
        )
    )

    fig.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=11),
        margin=dict(l=24, r=12, t=10, b=26),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(showgrid=False, tickfont=dict(color="#94a3b8")),
        yaxis=dict(showgrid=True, gridcolor="rgba(148,163,184,0.12)", zeroline=False),
        bargap=0.24,
        bargroupgap=0.1,
    )

    return fig.to_dict()
