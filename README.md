# AvalancheRiskAreaMapper
A streamlit app designed to allow mountain experts to draw avalanche release area boundaries on their regions of expertise

## Run locally

1. Create and activate a Python environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
streamlit run app.py
```

## App features

- Title: **Avalanche Risk Area Mapping Tool**
- Interactive map centered on the South Island of New Zealand
- Single **Area of Expertise** polygon
- Multiple **Potential Avalanche Release Area** polygons (inside the expertise area)
- **Clear All** to remove release area polygons
- **Download results** to export both layers as shapefiles in a ZIP archive
