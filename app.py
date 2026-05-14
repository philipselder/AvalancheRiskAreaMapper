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


def load_passwords() -> list[dict]:
    """Load credentials from passwords.json file."""
    passwords_file = Path(__file__).parent / "passwords.json"
    if passwords_file.exists():
        with open(passwords_file, "r") as f:
            return json.load(f)
    return []


def check_credentials(username: str, password: str) -> bool:
    """Check if username and password match any entry in passwords.json."""
    credentials = load_passwords()
    for cred in credentials:
        if cred.get("username") == username and cred.get("password") == password:
            return True
    return False


def init_state() -> None:
    defaults = {
        "logged_in": False,
        "login_error": "",
        "release_features": [],
        "selected_release_id": None,
        "next_release_id": 1,
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


def build_zip_bytes(release_features: list[dict]) -> bytes:
    release_rows = []
    for index, feature in enumerate(release_features):
        properties = feature.get("properties", {})
        release_rows.append(
            {
                "id": index + 1,
                "name": properties.get("name", ""),
                "desc": properties.get("description", ""),
                "geometry": shape(feature["geometry"]),
            }
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        release_gdf = gpd.GeoDataFrame(
            release_rows,
            crs="EPSG:4326",
        )
        release_base = tmp_path / "potential_avalanche_release_areas"
        release_gdf.to_file(f"{release_base}.shp")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for stem in ["potential_avalanche_release_areas"]:
                for file_path in tmp_path.glob(f"{stem}.*"):
                    zip_file.write(file_path, arcname=file_path.name)

        zip_buffer.seek(0)
        return zip_buffer.getvalue()


def draw_control_for_mode() -> dict:
    return {
        "polyline": False,
        "rectangle": False,
        "circle": False,
        "marker": False,
        "circlemarker": False,
        "polygon": True,
    }


def geometry_matches(geom1: dict, geom2: dict) -> bool:
    """Check if two geometries are approximately the same using Shapely."""
    if geom1.get("type") != geom2.get("type"):
        return False
    
    try:
        shape1 = shape(geom1)
        shape2 = shape(geom2)
        # Use almost_equals to allow for minor coordinate differences
        return shape1.almost_equals(shape2, decimal=5)
    except Exception:
        return False


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

    for release_feature in st.session_state.release_features:
        properties = release_feature.get("properties", {})
        release_id = properties.get("release_id", "")
        name = properties.get("name", "")
        display_name = name if name else f"PRA {release_id}"

        folium.GeoJson(
            release_feature,
            name="Potential Avalanche Release Area",
            tooltip=f"PRA_ID:{release_id}",
            popup=f"{display_name}\n{properties.get('description', '')}",
            style_function=lambda _feature: {
                "color": "#d62728",
                "weight": 2,
                "fillColor": "#d62728",
                "fillOpacity": 0.25,
            },
        ).add_to(feature_group)

    feature_group.add_child(AutoEnablePolygonDraw())

    return feature_group


def get_release_index_by_id(release_id: int | None) -> int | None:
    if release_id is None:
        return None

    for index, feature in enumerate(st.session_state.release_features):
        properties = feature.get("properties", {})
        if properties.get("release_id") == release_id:
            return index

    return None


def parse_release_id_from_tooltip(tooltip_value: str | None) -> int | None:
    if not tooltip_value:
        return None
    if not tooltip_value.startswith("PRA_ID:"):
        return None

    try:
        return int(tooltip_value.split(":", maxsplit=1)[1])
    except ValueError:
        return None


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


# Login UI
if not st.session_state.logged_in:
    st.divider()
    st.subheader("Login Required")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        
        if st.button("Login", use_container_width=True):
            if check_credentials(username, password):
                st.session_state.logged_in = True
                st.session_state.login_error = ""
                st.rerun()
            else:
                st.session_state.login_error = "Invalid username or password"
        
        if st.session_state.login_error:
            st.error(st.session_state.login_error)
    
    st.stop()


# Logout button (only show when logged in)
if st.session_state.logged_in:
    col1, col2 = st.columns([0.9, 0.1])
    with col2:
        if st.button("Log Out", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.login_error = ""
            st.rerun()


if st.session_state.show_thank_you:
    st.toast(
        "Thank you for your time! Please email the zipped files to "
        "eldph464@student.otago.ac.nz.",
        icon="✅",
    )
    st.session_state.show_thank_you = False

st.info("Draw potential avalanche release polygons. Click a polygon to edit its Name and Description.")

# st.selectbox(
#     "Basemap",
#     options=["Default", "Satellite Imagery", "Terrain"],
#     key="selected_basemap",
# )

map_key = "main_map"
sync_map_view_from_widget_state(map_key)

map_col, form_col = st.columns([3, 1])

with map_col:
    map_object = folium.Map(
        location=st.session_state.map_center,
        zoom_start=st.session_state.map_zoom,
        tiles=None,
    )
    add_basemap_layers(map_object, st.session_state.selected_basemap)

    Draw(
        export=False,
        draw_options=draw_control_for_mode(),
        edit_options={"edit": True, "remove": True},
    ).add_to(map_object)

    folium.LayerControl(collapsed=True).add_to(map_object)

    map_data = st_folium(
        map_object,
        width=1600,
        height=800,
        key=map_key,
        returned_objects=["last_active_drawing", "last_object_clicked_tooltip"],
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

        # Check if this is an edit of an existing feature
        edited_index = None
        for idx, existing_feature in enumerate(st.session_state.release_features):
            if geometry_matches(latest_feature["geometry"], existing_feature["geometry"]):
                edited_index = idx
                break

        if edited_index is not None:
            # Update geometry of existing feature, keep properties
            st.session_state.release_features[edited_index]["geometry"] = latest_feature["geometry"]
        else:
            # New feature
            release_id = st.session_state.next_release_id
            st.session_state.next_release_id += 1
            latest_feature["properties"] = {
                "release_id": release_id,
                "name": "",
                "description": "",
            }
            st.session_state.release_features.append(latest_feature)
            st.session_state.selected_release_id = release_id

        st.rerun()

clicked_release_id = parse_release_id_from_tooltip(map_data.get("last_object_clicked_tooltip"))
if clicked_release_id is not None:
    st.session_state.selected_release_id = clicked_release_id

with form_col:
    st.subheader("Polygon Details")
    selected_index = get_release_index_by_id(st.session_state.selected_release_id)

    if selected_index is None:
        st.caption("Click a drawn polygon to edit its details.")
    else:
        selected_feature = st.session_state.release_features[selected_index]
        selected_properties = selected_feature.get("properties", {})
        st.caption(f"Selected polygon: PRA {selected_properties.get('release_id')}")

        name_value = st.text_input(
            "Name",
            value=selected_properties.get("name", ""),
            key=f"name_input_{selected_properties.get('release_id')}",
        )
        description_value = st.text_area(
            "Description",
            value=selected_properties.get("description", ""),
            key=f"description_input_{selected_properties.get('release_id')}",
            height=150,
        )

        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Save", use_container_width=True, key=f"save_{selected_properties.get('release_id')}"):
                st.session_state.release_features[selected_index]["properties"]["name"] = name_value.strip()
                st.session_state.release_features[selected_index]["properties"]["description"] = description_value.strip()
                st.success("Polygon details saved.")

        with col2:
            if st.button("Delete", use_container_width=True, key=f"delete_{selected_properties.get('release_id')}"):
                st.session_state.release_features.pop(selected_index)
                st.session_state.selected_release_id = None
                st.success("Polygon deleted.")
                st.rerun()

button_columns = st.columns(2)

if button_columns[0].button("Clear PRAs", use_container_width=True, disabled=not st.session_state.logged_in):
    st.session_state.release_features = []
    st.session_state.selected_release_id = None
    st.session_state.next_release_id = 1
    st.session_state.last_processed_signature = None
    st.rerun()

can_download = (
    st.session_state.logged_in
    and len(st.session_state.release_features) > 0
)

if can_download:
    zip_payload = build_zip_bytes(st.session_state.release_features)
    download_clicked = button_columns[1].download_button(
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
    button_columns[1].button(
        "Download results",
        use_container_width=True,
        disabled=True,
        help="Draw at least one release area first.",
    )