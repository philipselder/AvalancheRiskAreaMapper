import io
import json
from datetime import datetime
import smtplib
import ssl
import tempfile
import zipfile
import re
from email.message import EmailMessage
from html import escape
from pathlib import Path

from branca.element import MacroElement, Template
import folium
import geopandas as gpd
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import shape
from streamlit_folium import st_folium


st.set_page_config(page_title="Avalanche Release Area Mapping Tool", layout="wide")
# st.title("Avalanche Release Area Mapping Tool")


RESULTS_RECIPIENT = "philip.s.elder@gmail.com"
RESULTS_ZIP_FILENAME = "avalanche_risk_area_mapping_results.zip"

# Local testing toggle: set True to use hard-coded SMTP credentials when secrets are unavailable.
USE_LOCAL_SMTP_TESTING = False
LOCAL_SMTP_CONFIG = {}


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


def get_user_by_credentials(username: str, password: str) -> dict | None:
    """Return the matched user record for valid credentials."""
    for cred in load_passwords():
        if cred.get("username") == username and cred.get("password") == password:
            return cred
    return None


def get_user_by_username(username: str) -> dict | None:
    """Return a user record by username."""
    for cred in load_passwords():
        if cred.get("username") == username:
            return cred
    return None


def init_state() -> None:
    defaults = {
        "logged_in": False,  # Set to False in production
        "login_error": "",
        "logged_in_username": "",
        "logged_in_email": "",
        "show_request_account_dialog": False,
        "clear_request_account_form": False,
        "release_features": [],
        "selected_release_id": None,
        "next_release_id": 1,
        "selected_basemap": "Default",
        "map_center": [-44.0, 170.5],
        "map_zoom": 6,
        "last_processed_signature": None,
        "map_refresh_counter": 0,
        "ignored_active_drawing_signature": None,
        "show_about_on_login": False,
        "show_about_dialog": False,
        "about_page": 0,
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


def get_smtp_config() -> dict:
    """Read SMTP settings from Streamlit secrets for deployment-safe email sending."""
    try:
        smtp_config = st.secrets.get("smtp")
    except Exception:
        smtp_config = None

    if not smtp_config and USE_LOCAL_SMTP_TESTING:
        smtp_config = LOCAL_SMTP_CONFIG

    if not smtp_config:
        raise ValueError(
            "Missing [smtp] config in Streamlit secrets. Add host, port, username, password, and from_email, "
            "or set USE_LOCAL_SMTP_TESTING=True with LOCAL_SMTP_CONFIG values for local testing."
        )

    required_keys = ["host", "port", "username", "password", "from_email"]
    missing = [key for key in required_keys if key not in smtp_config]
    if missing:
        raise ValueError(f"Missing SMTP secrets: {', '.join(missing)}")

    use_starttls_value = smtp_config.get("use_starttls", True)
    if isinstance(use_starttls_value, str):
        use_starttls = use_starttls_value.strip().lower() in {"1", "true", "yes", "on"}
    else:
        use_starttls = bool(use_starttls_value)

    return {
        "host": smtp_config["host"],
        "port": int(smtp_config["port"]),
        "username": smtp_config["username"],
        "password": smtp_config["password"],
        "from_email": smtp_config["from_email"],
        "use_starttls": use_starttls,
    }


def get_submitter_identity() -> tuple[str, str]:
    """Resolve submitter name/email from the authenticated user record."""
    username = st.session_state.get("logged_in_username", "").strip()
    email = st.session_state.get("logged_in_email", "").strip()

    if username and email:
        return username, email

    if username:
        user_record = get_user_by_username(username)
        if user_record:
            resolved_email = str(user_record.get("email", "")).strip()
            return username, resolved_email

    return username, email


def build_results_zip_filename(username: str) -> str:
    """Build a timestamped zip filename for the current user."""
    safe_username = re.sub(r"[^A-Za-z0-9_-]+", "_", username.strip()).strip("_")
    if not safe_username:
        safe_username = "unknown_user"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"avalanche_risk_area_mapping_results_{safe_username}_{timestamp}.zip"


def bootstrap_logged_in_identity() -> None:
    """Fill submitter identity for local/dev sessions that bypass login."""
    if not st.session_state.get("logged_in"):
        return

    existing_username = st.session_state.get("logged_in_username", "").strip()
    existing_email = st.session_state.get("logged_in_email", "").strip()
    if existing_username and existing_email:
        return

    credentials = load_passwords()
    if not credentials:
        return

    first_user = credentials[0]
    st.session_state.logged_in_username = str(first_user.get("username", "")).strip()
    st.session_state.logged_in_email = str(first_user.get("email", "")).strip()



def send_results_email(
    zip_payload: bytes,
    submitter_name: str,
    submitter_email: str,
    comments: str = "",
) -> None:
    """Send the generated results zip as an email attachment."""
    submitter_name = submitter_name.strip()
    submitter_email = submitter_email.strip()
    comments = comments.strip()

    body_lines = [
        "Avalanche Release Area Mapper submission attached.",
        "",
        f"Submitter name: {submitter_name if submitter_name else 'Not provided'}",
        f"Submitter email: {submitter_email if submitter_email else 'Not provided'}",
    ]
    if comments:
        body_lines.extend(["", "Comments:", comments])

    send_email_message(
        subject=f"Avalanche Release Area Mapper Results - {submitter_name or 'Unknown Submitter'}",
        body="\n".join(body_lines),
        attachments=[
            {
                "payload": zip_payload,
                "maintype": "application",
                "subtype": "zip",
                "filename": build_results_zip_filename(submitter_name),
            }
        ],
    )


def send_email_message(
    subject: str,
    body: str,
    attachments: list[dict] | None = None,
) -> None:
    """Send an email using configured SMTP settings with optional attachments."""
    smtp_config = get_smtp_config()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_config["from_email"]
    message["To"] = RESULTS_RECIPIENT
    message.set_content(body)

    for attachment in attachments or []:
        message.add_attachment(
            attachment["payload"],
            maintype=attachment["maintype"],
            subtype=attachment["subtype"],
            filename=attachment["filename"],
        )

    smtp_context = ssl.create_default_context()
    with smtplib.SMTP(smtp_config["host"], smtp_config["port"], timeout=30) as server:
        server.ehlo()
        if smtp_config["use_starttls"]:
            server.starttls(context=smtp_context)
            server.ehlo()
        server.login(smtp_config["username"], smtp_config["password"])
        server.send_message(message)


def send_account_request_email(
    first_name: str,
    last_name: str,
    email_address: str,
    job_title: str,
    organization: str,
    avalanche_work_description: str,
) -> None:
    """Send account request details to the configured results recipient."""
    body_lines = [
        "New account request for Avalanche Release Area Mapping Tool.",
        "",
        f"First Name: {first_name}",
        f"Last Name: {last_name}",
        f"Email Address: {email_address}",
        f"Job Title: {job_title}",
        f"Organization: {organization}",
        "",
        "Describe your work with avalanches:",
        avalanche_work_description,
    ]

    requester_name = f"{first_name} {last_name}".strip()
    send_email_message(
        subject=f"Avalanche Mapper Account Request - {requester_name or 'Unknown Requester'}",
        body="\n".join(body_lines),
    )


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

        # Render saved polygon names directly on the map using an interior label point.
        label_text = name.strip()
        if label_text:
            geometry_shape = shape(release_feature["geometry"])
            label_point = geometry_shape.representative_point()
            folium.Marker(
                location=[label_point.y, label_point.x],
                icon=folium.DivIcon(
                    html=(
                        "<div style=\""
                        "font-size:12px;"
                        "font-weight:600;"
                        "color:#111827;"
                        "background:rgba(255,255,255,0.9);"
                        "padding:2px 6px;"
                        "border-radius:4px;"
                        "border:1px solid rgba(17,24,39,0.2);"
                        "white-space:nowrap;"
                        "\">"
                        f"{escape(label_text)}"
                        "</div>"
                    )
                ),
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


def refresh_map_and_clear_cache(map_key: str) -> None:
    """Clear the map cache while preserving current zoom and center."""
    # Preserve current map view
    widget_state = st.session_state.get(map_key)
    stale_signature = None
    if isinstance(widget_state, dict):
        center = widget_state.get("center")
        if isinstance(center, dict) and "lat" in center and "lng" in center:
            st.session_state.map_center = [center["lat"], center["lng"]]
        zoom = widget_state.get("zoom")
        if isinstance(zoom, int):
            st.session_state.map_zoom = zoom

        stale_feature = widget_state.get("last_active_drawing")
        if is_polygon_feature(stale_feature):
            stale_signature = feature_signature(stale_feature)
    
    # Force a full widget remount so Leaflet draw cache is discarded.
    st.session_state.map_refresh_counter += 1

    # Ignore only the specific stale draw callback from the previous widget.
    st.session_state.ignored_active_drawing_signature = stale_signature
    st.session_state.last_processed_signature = None


init_state()
bootstrap_logged_in_identity()


@st.dialog("Login Required")
def login_dialog() -> None:
    """Dialog for user authentication."""
    
    username = st.text_input("Username", key="login_username")
    password = st.text_input("Password", type="password", key="login_password")
    
    if st.button("Login", use_container_width=True):
        user_record = get_user_by_credentials(username, password)
        if user_record:
            st.session_state.logged_in = True
            st.session_state.login_error = ""
            st.session_state.logged_in_username = str(user_record.get("username", "")).strip()
            st.session_state.logged_in_email = str(user_record.get("email", "")).strip()
            st.session_state.show_about_on_login = True
            st.rerun()
        else:
            st.session_state.login_error = "Invalid username or password"

    st.markdown("Don't have an account? [Request one now!](?request_account=1)")
    
    if st.session_state.login_error:
        st.error(st.session_state.login_error)


@st.dialog("Request an Account")
def request_account_dialog() -> None:
    st.write("Complete this form to request an account.")

    first_name = st.text_input("First Name", key="request_first_name")
    last_name = st.text_input("Last Name", key="request_last_name")
    email_address = st.text_input("Email Address", key="request_email_address")
    job_title = st.text_input("Job Title", key="request_job_title")
    organization = st.text_input("Organization", key="request_organization")
    avalanche_work_description = st.text_area(
        "Describe your work with avalanches",
        key="request_avalanche_work_description",
        height=140,
    )

    submit_col, cancel_col = st.columns([1, 1])
    with submit_col:
        if st.button("Submit Request", type="primary", use_container_width=True):
            required_values = {
                "First Name": first_name.strip(),
                "Last Name": last_name.strip(),
                "Email Address": email_address.strip(),
                "Job Title": job_title.strip(),
                "Organization": organization.strip(),
                "Describe your work with avalanches": avalanche_work_description.strip(),
            }
            missing_fields = [label for label, value in required_values.items() if not value]

            if missing_fields:
                st.error(f"Please complete all fields: {', '.join(missing_fields)}")
            else:
                with st.spinner("Submitting request..."):
                    try:
                        send_account_request_email(
                            first_name=required_values["First Name"],
                            last_name=required_values["Last Name"],
                            email_address=required_values["Email Address"],
                            job_title=required_values["Job Title"],
                            organization=required_values["Organization"],
                            avalanche_work_description=required_values["Describe your work with avalanches"],
                        )
                    except Exception as exc:
                        st.error(f"Could not submit request: {exc}")
                    else:
                        st.success("Request submitted. You will be contacted once your account is created.")
                        st.session_state.show_request_account_dialog = False
                        st.session_state.clear_request_account_form = True
                        st.rerun()

    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.session_state.show_request_account_dialog = False
            st.rerun()


@st.dialog("About This Tool")
def about_dialog() -> None:
    tutorial_pages = [
        {
            "text": (
                "Welcome to the Avalanche Release Area Mapping Tool! This application allows you to draw "
                "potential avalanche release areas on an interactive map, add details about each area, and "
                "submit your findings for review and enhancement. Your contributions help improve avalanche "
                "forecasting and mapping products, ultimately supporting safer backcountry experiences. Click "
                "'Next' for a brief tutorial on how to use the tool."
            ),
            "gif": None,
        },
        {
            "text": (
                "To draw polygons, click the little pentagon icon button on the left of the map, then click "
                "your vertices. Double-click to finish."
            ),
            "gif": Path(__file__).parent / "resources" / "tutorial_1.gif",
        },
        {
            "text": (
                "Once the shape is complete, enter the name and description of the shape, including any "
                "historical avalanche activity, and click Save."
            ),
            "gif": Path(__file__).parent / "resources" / "tutorial_2.gif",
        },
        {
            "text": "Feel free to change the basemaps on the right to help visualize different terrain features.",
            "gif": Path(__file__).parent / "resources" / "tutorial_3.gif",
        },
        {
            "text": (
                'Once you\'re finished drawing all of your release areas, click the "Send Results" button at '
                "the top. REMEMBER: we are looking for potential release areas, not runout zones or other "
                "features. Focus on identifying the source areas where avalanches are likely to initiate."
            ),
            "gif": Path(__file__).parent / "resources" / "tutorial_4.gif",
        },
    ]

    total_pages = len(tutorial_pages)
    current_page = max(0, min(st.session_state.about_page, total_pages - 1))
    st.session_state.about_page = current_page
    page_content = tutorial_pages[current_page]

    st.caption(f"Tutorial {current_page + 1} of {total_pages}")
    st.write(page_content["text"])

    page_gif = page_content["gif"]
    if page_gif:
        st.image(str(page_gif), use_container_width=True)

    prev_col, _, next_col = st.columns([1, 3, 1])
    with prev_col:
        if st.button("\u2190 Prev", use_container_width=True, disabled=current_page == 0):
            st.session_state.about_page = current_page - 1
            st.session_state.show_about_dialog = True
            st.rerun()
    with next_col:
        next_label = "Done" if current_page == total_pages - 1 else "Next \u2192"
        if st.button(next_label, use_container_width=True):
            if current_page < total_pages - 1:
                st.session_state.about_page = current_page + 1
                st.session_state.show_about_dialog = True
            else:
                st.session_state.show_about_dialog = False
            st.rerun()

    st.caption("Created by Philip Elder, University of Otago Geography Department, 2026.")

# Show login dialog if not authenticated
if not st.session_state.logged_in:
    if st.session_state.clear_request_account_form:
        for key in [
            "request_first_name",
            "request_last_name",
            "request_email_address",
            "request_job_title",
            "request_organization",
            "request_avalanche_work_description",
        ]:
            st.session_state.pop(key, None)
        st.session_state.clear_request_account_form = False

    request_account_param = str(st.query_params.get("request_account", "")).strip().lower()
    if request_account_param in {"1", "true", "yes", "on"}:
        st.session_state.show_request_account_dialog = True
        if "request_account" in st.query_params:
            del st.query_params["request_account"]

    if st.session_state.show_request_account_dialog:
        request_account_dialog()
    else:
        login_dialog()
    st.stop()

# Show about dialog automatically after first login
if st.session_state.show_about_on_login:
    st.session_state.show_about_on_login = False
    st.session_state.about_page = 0
    st.session_state.show_about_dialog = True

if st.session_state.show_about_dialog:
    st.session_state.show_about_dialog = False
    about_dialog()

@st.dialog("Send Results")
def send_results(zip_payload: bytes) -> None:
    submitter_name, submitter_email = get_submitter_identity()
    st.write(
        f"The current release areas will be zipped and sent via email for processing."
        " They will be reviewed and enhanced, and sent back to the submitter for use in avalanche forecasting and mapping products."
        " Submitter details are pulled from the logged-in account."
        " Thanks for contributing to avalanche safety!"
    )
    st.caption(f"Submitter Name: {submitter_name or 'Not available'}")
    st.caption(f"Submitter Email: {submitter_email or 'Not available'}")
    comments = st.text_area("Comments (optional)", key="results_comments", height=120)

    if st.button("Send Email", type="primary", use_container_width=True):
        with st.spinner("Sending email..."):
            try:
                send_results_email(zip_payload, submitter_name, submitter_email, comments)
            except Exception as exc:
                st.error(f"Could not send email: {exc}")
            else:
                st.success(f"Release areas sent! You will receive the results soon at {submitter_email if submitter_email else 'your email address'}.")

map_key = f"main_map_{st.session_state.map_refresh_counter}"

# Logout button (only show when logged in)
col0, col1, col2, col3, col4 = st.columns([0.65, 0.1, 0.08, 0.08, 0.08])
with col0:
    st.markdown("# Avalanche Release Area Mapping Tool")
with col1:
    can_download = (
        st.session_state.logged_in
        and len(st.session_state.release_features) > 0
    )

    if can_download:
        zip_payload = build_zip_bytes(st.session_state.release_features)
        if col1.button(
            "Send Results",
            use_container_width=True,
            help=f"Finished? Send your release areas to be processed.",
        ):
            send_results(zip_payload)
    else:
        col1.button(
            "Send Results",
            use_container_width=True,
            disabled=True,
            help="Draw at least one release area first.",
        )
with col2:
    if st.button("Clear PRAs", use_container_width=True, disabled=not st.session_state.logged_in, help="Clear all potential release area polygons."):
        st.session_state.release_features = []
        st.session_state.selected_release_id = None
        st.session_state.next_release_id = 1
        refresh_map_and_clear_cache(map_key)
        st.rerun()
with col3:
    if st.button("About", use_container_width=True, help="Learn more about how to use this tool."):
        st.session_state.about_page = 0
        st.session_state.show_about_dialog = True
        st.rerun()
with col4:
    if st.button("Log Out", use_container_width=True, help="Log out of the application."):
        st.session_state.logged_in = False
        st.session_state.login_error = ""
        st.session_state.logged_in_username = ""
        st.session_state.logged_in_email = ""
        st.rerun()


st.info("Draw potential avalanche release polygons by clicking the little pentagon icon on the left. Double-click to finish drawing.")

# st.selectbox(
#     "Basemap",
#     options=["Default", "Satellite Imagery", "Terrain"],
#     key="selected_basemap",
# )


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
        edit_options={"edit": False, "remove": False},
    ).add_to(map_object)

    folium.LayerControl(collapsed=False).add_to(map_object)

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
    if signature == st.session_state.ignored_active_drawing_signature:
        st.session_state.ignored_active_drawing_signature = None
    else:
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
                st.rerun()

        with col2:
            if st.button("Delete", use_container_width=True, key=f"delete_{selected_properties.get('release_id')}"):
                st.session_state.release_features.pop(selected_index)
                st.session_state.selected_release_id = None
                st.success("Polygon deleted.")
                refresh_map_and_clear_cache(map_key)
                st.rerun()