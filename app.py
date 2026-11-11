from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

DATA = Path(__file__).resolve().parent / "data"

COLORS = ["#edf8fb", "#b2e2e2", "#66c2a4", "#2ca25f", "#006d2c"]
LABELS = ["0–<5", "5–<10", "10–<15", "15–<20", "20+"]

UNMATCHED_REASONS = {
    "Manhattan - Vesey St, NY": "Gap along the simplified Hudson River shoreline.",
    "McDermitt, NV": "Gap near the simplified Nevada–Oregon border.",
    "South Padre Island, TX": "Gap along the simplified Texas barrier-island coastline.",
}


@st.cache_data
def load_data():
    sites = pd.read_csv(DATA / "superchargers_us.csv").rename(
        columns={"state": "reported_state"}
    )
    states = gpd.read_file(DATA / "states.geojson").to_crs("EPSG:4326")
    states = states[["fips", "state", "geometry"]].rename(columns={"state": "region"})

    population = pd.read_csv(DATA / "state_stats.csv", dtype={"fips": "string"})
    states["fips"] = states["fips"].astype("string").str.zfill(2)
    population["fips"] = population["fips"].str.zfill(2)

    # Spatial join: assign each station to the region that contains it.
    points = gpd.GeoDataFrame(
        sites,
        geometry=gpd.points_from_xy(sites["lon"], sites["lat"]),
        crs=states.crs,
    )
    tagged = gpd.sjoin(points, states, how="left", predicate="within").drop(
        columns="index_right"
    )
    counts = tagged.groupby("fips").size()

    # Attribute join: attach population to each region using its FIPS code.
    regions = states.merge(
        population[["fips", "pop_total"]],
        on="fips",
        how="left",
        validate="one_to_one",
        indicator="population_join",
    )
    regions["sites"] = regions["fips"].map(counts).fillna(0).astype(int)
    regions["rate"] = (
        regions["sites"] * 1_000_000
        / regions["pop_total"].where(regions["pop_total"] > 0)
    )
    regions["rate_label"] = regions["rate"].map(
        lambda rate: f"{rate:.2f}" if pd.notna(rate) else "Unavailable"
    )

    return tagged, regions


def make_map(regions, points, selected):
    map_view = folium.Map(
        location=[39.5, -98.4],
        zoom_start=4,
        tiles="OpenStreetMap",
        crs="EPSG3857",
    )

    def region_style(feature):
        rate = feature["properties"]["rate"]
        color = "#bdbdbd" if rate is None else COLORS[min(int(rate // 5), 4)]
        return {
            "fillColor": color,
            "fillOpacity": 0.72,
            "color": "#55636d",
            "weight": 1,
        }

    folium.GeoJson(
        regions[["region", "sites", "rate", "rate_label", "geometry"]].to_json(),
        name="Sites per million residents",
        style_function=region_style,
        tooltip=folium.GeoJsonTooltip(
            fields=["region", "sites", "rate_label"],
            aliases=["Region", "Matched sites", "Sites per million"],
        ),
    ).add_to(map_view)

    if not points.empty:
        folium.GeoJson(
            points[["name", "stalls", "geometry"]].to_json(),
            name="Supercharger sites",
            marker=folium.CircleMarker(
                radius=3,
                color="#5b2417",
                weight=0.6,
                fill=True,
                fill_color="#c44e27",
                fill_opacity=0.85,
            ),
            tooltip=folium.GeoJsonTooltip(
                fields=["name", "stalls"], aliases=["Site", "Stalls"]
            ),
        ).add_to(map_view)

    if selected == "Alaska":
        map_view.location = [63, -151]
    elif selected != "All regions":
        west, south, east, north = regions.total_bounds
        map_view.fit_bounds([[south, west], [north, east]], max_zoom=9)

    folium.LayerControl(collapsed=False).add_to(map_view)
    return map_view


st.set_page_config(page_title="U.S. Supercharger Sites", layout="wide")
st.title("Where are U.S. Supercharger sites?")
st.write("Explore charging locations and compare sites per million residents.")

points, regions = load_data()
matched = points[points["fips"].notna()]
unmatched = points[points["fips"].isna()].copy()

selected = st.sidebar.selectbox("Region", ["All regions", *sorted(regions["region"])])
st.sidebar.caption("Data source: DATS 6401 Week 10 course files.")

visible_regions = regions
visible_points = matched
if selected != "All regions":
    visible_regions = regions[regions["region"] == selected]
    visible_points = matched[matched["region"] == selected]

map_tab, joins_tab = st.tabs(["Map", "Data & joins"])

with map_tab:
    sites_metric, regions_metric = st.columns(2)
    sites_metric.metric("Mapped sites", len(visible_points))
    regions_metric.metric("Regions shown", len(visible_regions))

    legend = " &nbsp; ".join(
        f'<span style="color:{color};text-shadow:0 0 1px #444">■</span> '
        f'{label.replace("<", "&lt;")}'
        for color, label in zip(COLORS, LABELS)
    )
    st.markdown(f"**Sites per million residents** &nbsp; {legend}", unsafe_allow_html=True)
    st_folium(
        make_map(visible_regions, visible_points, selected),
        key=f"map-{selected}",
        use_container_width=True,
        height=540,
        returned_objects=[],
    )
    st.caption(
        "Polygon colors show matched Supercharger sites per million residents. "
        "Orange dots show individual matched sites. The map uses Web Mercator "
        "(EPSG:3857) and five fixed classes with cutoffs at 5, 10, 15, and 20. "
        "Gray indicates an unavailable rate. Sites that matched no region are "
        "listed under Data & joins and excluded from the map and rates. "
        "The overview centers on the contiguous U.S. Use the region filter "
        "to zoom to Alaska, Hawaii, or Puerto Rico."
    )

with joins_tab:
    st.subheader("Spatial join")
    st.write(
        f"**{len(matched):,} of {len(points):,}** sites matched a region. "
        f"**{len(unmatched):,}** matched no region. The spatial left join "
        "uses longitude, latitude, and the `within` predicate."
    )
    st.markdown(f"**Matched sites in view ({len(visible_points):,})**")
    st.dataframe(
        visible_points[["name", "reported_state", "region", "fips", "lat", "lon", "stalls"]],
        hide_index=True,
    )

    if not unmatched.empty:
        st.markdown("**Unmatched sites across the full dataset**")
        st.write("The Week 10 lecture attributes the three gaps to simplified boundaries.")
        unmatched["reason"] = unmatched["name"].map(UNMATCHED_REASONS).fillna(
            "No containing polygon in the supplied boundaries. Cause unconfirmed."
        )
        st.dataframe(
            unmatched[["name", "reported_state", "lat", "lon", "reason"]],
            hide_index=True,
        )

    st.subheader("Attribute join")
    unmatched_regions = regions["population_join"].ne("both").sum()
    st.write(
        "The attribute left join attaches population using two-digit FIPS codes. "
        f"**{unmatched_regions} of {len(regions)}** regions had no population match."
    )
    st.markdown("**Sites per million = matched sites ÷ population × 1,000,000**")
    st.dataframe(
        visible_regions[["region", "fips", "sites", "pop_total", "rate", "population_join"]]
        .sort_values("rate", ascending=False)
        .rename(columns={
            "region": "Region",
            "fips": "FIPS",
            "sites": "Matched sites",
            "pop_total": "Population",
            "rate": "Sites per million",
            "population_join": "Population join",
        }),
        column_config={"Sites per million": st.column_config.NumberColumn(format="%.2f")},
        hide_index=True,
    )