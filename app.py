import io
import json
import tempfile
import zipfile
from pathlib import Path

from branca.element import MacroElement, Template
import folium
import geopandas as gpd
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import shape
from streamlit_folium import st_folium


st.set_page_config(page_title="Avalanche Risk Area Mapping Tool", layout="wide")
st.title("Avalanche Risk Area Mapping Tool")


def init_state() -> None:
    defaults = {
        "expertise_feature": None,
        "release_features": [],
        "draw_mode": None,
        "selected_basemap": "Default",
        "map_center": [-44.0, 170.5],
        "map_zoom": 6,
        "last_processed_signature": None,
        "show_thank_you": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def feature_signature(feature: dict) -> str:
    return json.dumps(feature, sort_keys=True)


def is_polygon_feature(feature: dict | None) -> bool:
    if not feature:
        return False
    geometry = feature.get("geometry", {})
    return geometry.get("type") in {"Polygon", "MultiPolygon"}


def build_zip_bytes(expertise_feature: dict, release_features: list[dict]) -> bytes:
    expertise_geom = shape(expertise_feature["geometry"])
    release_geoms = [shape(feature["geometry"]) for feature in release_features]

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        expertise_gdf = gpd.GeoDataFrame(
            [{"id": 1}],
            geometry=[expertise_geom],
            crs="EPSG:4326",
        )
        expertise_base = tmp_path / "area_of_expertise"
        expertise_gdf.to_file(f"{expertise_base}.shp")

        release_gdf = gpd.GeoDataFrame(
            [{"id": index + 1} for index in range(len(release_geoms))],
            geometry=release_geoms,
            crs="EPSG:4326",
        )
        release_base = tmp_path / "potential_avalanche_release_areas"
        release_gdf.to_file(f"{release_base}.shp")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for stem in ["area_of_expertise", "potential_avalanche_release_areas"]:
                for file_path in tmp_path.glob(f"{stem}.*"):
                    zip_file.write(file_path, arcname=file_path.name)

        zip_buffer.seek(0)
        return zip_buffer.getvalue()


def draw_control_for_mode(mode: str | None) -> dict:
    return {
        "polyline": False,
        "rectangle": False,
        "circle": False,
        "marker": False,
        "circlemarker": False,
        "polygon": True,
    }


class AutoEnablePolygonDraw(MacroElement):
    _template = Template(
        """
        {% macro script(this, kwargs) %}
        setTimeout(function() {
            if (!window.map || !window.map._controls) {
                return;
            }

            const drawControl = window.map._controls.find(function(control) {
                return control
                    && control._toolbars
                    && control._toolbars.draw
                    && control._toolbars.draw._modes
                    && control._toolbars.draw._modes.polygon;
            });

            if (!drawControl) {
                return;
            }

            const polygonMode = drawControl._toolbars.draw._modes.polygon;
            if (polygonMode.handler && !polygonMode.handler.enabled()) {
                polygonMode.handler.enable();
            }
        }, 0);
        {% endmacro %}
        """
    )


def build_feature_group() -> folium.FeatureGroup:
    feature_group = folium.FeatureGroup(name="Mapped Areas")

    if st.session_state.expertise_feature:
        folium.GeoJson(
            st.session_state.expertise_feature,
            name="Area of Expertise",
            style_function=lambda _feature: {
                "color": "#1f77b4",
                "weight": 3,
                "fillColor": "#1f77b4",
                "fillOpacity": 0.15,
            },
        ).add_to(feature_group)

    for release_feature in st.session_state.release_features:
        folium.GeoJson(
            release_feature,
            name="Potential Avalanche Release Area",
            style_function=lambda _feature: {
                "color": "#d62728",
                "weight": 2,
                "fillColor": "#d62728",
                "fillOpacity": 0.25,
            },
        ).add_to(feature_group)

    if st.session_state.draw_mode in {"draw_expertise", "draw_release"}:
        feature_group.add_child(AutoEnablePolygonDraw())

    return feature_group


def add_basemap_layers(map_object: folium.Map, selected_basemap: str) -> None:
    # Folium is 2D; provide terrain-style relief instead of true 3D terrain.
    folium.TileLayer(
        tiles="OpenStreetMap",
        name="Default",
        control=True,
        show=selected_basemap == "Default",
    ).add_to(map_object)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles &copy; Esri",
        name="Satellite Imagery",
        control=True,
        show=selected_basemap == "Satellite Imagery",
    ).add_to(map_object)

    folium.TileLayer(
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr="Map data: &copy; OpenStreetMap contributors, SRTM | Map style: &copy; OpenTopoMap",
        name="Terrain",
        control=True,
        show=selected_basemap == "Terrain",
    ).add_to(map_object)


def sync_map_view_from_widget_state(map_key: str) -> None:
    widget_state = st.session_state.get(map_key)
    if not isinstance(widget_state, dict):
        return

    center = widget_state.get("center")
    if isinstance(center, dict) and "lat" in center and "lng" in center:
        st.session_state.map_center = [center["lat"], center["lng"]]

    zoom = widget_state.get("zoom")
    if isinstance(zoom, int):
        st.session_state.map_zoom = zoom


init_state()

if st.session_state.show_thank_you:
    st.toast(
        "Thank you for your time! Please email the zipped files to "
        "eldph464@student.otago.ac.nz.",
        icon="✅",
    )
    st.session_state.show_thank_you = False

if st.session_state.draw_mode == "draw_expertise":
    st.info("Draw one area of expertise polygon. Double-click the final point to finish.")
elif st.session_state.draw_mode == "draw_release":
    st.info("Draw potential avalanche release polygons inside the area of expertise. Double-click to finish each polygon.")

# st.selectbox(
#     "Basemap",
#     options=["Default", "Satellite Imagery", "Terrain"],
#     key="selected_basemap",
# )

map_key = "main_map"
sync_map_view_from_widget_state(map_key)

map_object = folium.Map(
    location=st.session_state.map_center,
    zoom_start=st.session_state.map_zoom,
    tiles=None,
)
add_basemap_layers(map_object, st.session_state.selected_basemap)

Draw(
    export=False,
    draw_options=draw_control_for_mode(st.session_state.draw_mode),
    edit_options={"edit": False, "remove": False},
).add_to(map_object)

folium.LayerControl(collapsed=True).add_to(map_object)

map_data = st_folium(
    map_object,
    width=2000,
    height=800,
    key=map_key,
    returned_objects=["last_active_drawing"],
    feature_group_to_add=build_feature_group(),
    center=tuple(st.session_state.map_center),
    zoom=st.session_state.map_zoom,
)

latest_feature = map_data.get("last_active_drawing")

if latest_feature and is_polygon_feature(latest_feature):
    signature = feature_signature(latest_feature)
    is_new_feature = signature != st.session_state.last_processed_signature

    if is_new_feature:
        st.session_state.last_processed_signature = signature

        if st.session_state.draw_mode == "draw_expertise" and st.session_state.expertise_feature is None:
            st.session_state.expertise_feature = latest_feature
            st.session_state.draw_mode = None
            st.rerun()

        if st.session_state.draw_mode == "draw_release" and st.session_state.expertise_feature is not None:
            expertise_geom = shape(st.session_state.expertise_feature["geometry"])
            release_geom = shape(latest_feature["geometry"])
            if expertise_geom.covers(release_geom):
                st.session_state.release_features.append(latest_feature)
                st.rerun()
            else:
                st.warning("Release areas must be inside the area of expertise polygon.")

button_columns = st.columns(4)

expertise_label = (
    "Clear Area of Expertise"
    if st.session_state.expertise_feature is not None
    else "Draw Area of Expertise"
)

if button_columns[0].button(expertise_label, use_container_width=True):
    if st.session_state.expertise_feature is None:
        st.session_state.draw_mode = "draw_expertise"
    else:
        st.session_state.expertise_feature = None
        st.session_state.release_features = []
        st.session_state.draw_mode = None
        st.session_state.last_processed_signature = None
    st.rerun()

if button_columns[1].button("Draw Potential Avalanche Release Area", use_container_width=True):
    if st.session_state.expertise_feature is None:
        st.warning("Draw an area of expertise first.")
    else:
        st.session_state.draw_mode = "draw_release"
        st.rerun()

if button_columns[2].button("Clear PRAs", use_container_width=True):
    st.session_state.release_features = []
    st.session_state.last_processed_signature = None
    st.rerun()

can_download = (
    st.session_state.expertise_feature is not None
    and len(st.session_state.release_features) > 0
)

if can_download:
    zip_payload = build_zip_bytes(
        st.session_state.expertise_feature,
        st.session_state.release_features,
    )
    download_clicked = button_columns[3].download_button(
        "Download results",
        data=zip_payload,
        file_name="avalanche_risk_area_mapping_results.zip",
        mime="application/zip",
        use_container_width=True,
    )
    if download_clicked:
        st.session_state.show_thank_you = True
        st.rerun()
else:
    button_columns[3].button(
        "Download results",
        use_container_width=True,
        disabled=True,
        help="Draw one area of expertise and at least one release area first.",
    )