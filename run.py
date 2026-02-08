"""
Reads .csv, enrich each row with full address and GPS via Google Places API,
and write a GeoJSON file.
Requires: GOOGLE_PLACES_API_KEY in environment (Places API and Geocoding API enabled).
"""

import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

try:
    import requests
except ImportError:
    raise SystemExit("Install requests: pip install requests")

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:
    raise SystemExit("tkinter is required for file dialogs (usually included with Python).")

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
BASE_PLACES_URL = "https://maps.googleapis.com/maps/api/place"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
REQUEST_DELAY_S = 0.2  # avoid rate limits
API_REQUEST_LIMIT = 1000  # max API requests per run (Find Place + Place Details)

# -----------------------------------------------------------------------------
# Extract place reference from Google Maps URL (optional, for future use)
# -----------------------------------------------------------------------------
def extract_place_ref_from_url(url):
    """Extract the 0x...:0x... token from a Google Maps place URL, if present."""
    if not url or "google.com/maps" not in url:
        return None
    m = re.search(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)", url)
    return m.group(1) if m else None


def ask_input_csv_path():
    """Show an open-file dialog to select the input CSV. Returns path or None if cancelled."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select input CSV (places with Google Maps URLs)",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        initialdir=os.path.dirname(os.path.abspath(__file__)),
        initialfile="Favourite places.csv",
    )
    root.destroy()
    return path if path else None


def ask_output_geojson_path(initial_dir=None, suggested_name="Favourite places.geojson"):
    """Show a save-file dialog to choose the output GeoJSON. Returns path or None if cancelled."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.asksaveasfilename(
        title="Save GeoJSON as",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        initialdir=initial_dir or os.path.dirname(os.path.abspath(__file__)),
        initialfile=suggested_name,
        defaultextension=".json",
    )
    root.destroy()
    return path if path else None


def get_api_key():
    key = os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        raise SystemExit(
            "Set GOOGLE_PLACES_API_KEY (or GOOGLE_MAPS_API_KEY) in your environment. "
            "Enable 'Places API' and 'Geocoding API' in Google Cloud Console."
        )
    return key


def find_place_id(api_key, title, maps_url):
    """Use Find Place from Text to get place_id for the given title; prefer text from URL if useful."""
    # Prefer the decoded place name from the URL path (e.g. "Westdam 59") for accuracy
    name_from_url = None
    if maps_url and "/place/" in maps_url:
        try:
            path = maps_url.split("?")[0]
            part = path.split("/place/")[-1].split("/data=")[0]
            if part:
                name_from_url = part.replace("+", " ").strip()
        except Exception:
            pass
    query = (name_from_url or title or "").strip()
    if not query:
        return None
    url = f"{BASE_PLACES_URL}/findplacefromtext/json"
    params = {
        "key": api_key,
        "input": query,
        "inputtype": "textquery",
        "fields": "place_id",
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "OK" or not data.get("candidates"):
        return None
    return data["candidates"][0].get("place_id")


def get_place_details(api_key, place_id):
    """Fetch geometry (lat/lng) and formatted_address for a place_id."""
    url = f"{BASE_PLACES_URL}/details/json"
    params = {
        "key": api_key,
        "place_id": place_id,
        "fields": "geometry,formatted_address,name,address_components",
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "OK":
        return None
    result = data.get("result", {})
    geom = result.get("geometry")
    if not geom or "location" not in geom:
        return None
    lat = geom["location"].get("lat")
    lng = geom["location"].get("lng")
    if lat is None or lng is None:
        return None
    address = result.get("formatted_address") or ""
    name = result.get("name") or ""
    # Optional: country code from address_components
    country_code = None
    for comp in result.get("address_components") or []:
        if "country" in (comp.get("types") or []):
            country_code = comp.get("short_name")
            break
    return {
        "lat": lat,
        "lng": lng,
        "address": address,
        "name": name,
        "country_code": country_code,
    }


def build_feature(row, details, now_iso):
    """Build one GeoJSON Feature in the same structure as Saved Places.json."""
    title = (row.get("Title") or "").strip()
    url = (row.get("URL") or "").strip()
    if details:
        coords = [details["lng"], details["lat"]]
        props = {
            "date": now_iso,
            "google_maps_url": url or f"http://maps.google.com/?q={quote_plus(title)}",
            "name": details["name"] or title,
            "location": {
                "address": details["address"],
            },
        }
        if details.get("country_code"):
            props["location"]["country_code"] = details["country_code"]
    else:
        coords = [0, 0]
        props = {
            "date": now_iso,
            "google_maps_url": url or "",
            "Comment": "No location information is available for this saved place",
        }
        if title:
            props["name"] = title
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coords},
        "properties": props,
    }


def main():
    csv_path = ask_input_csv_path()
    if not csv_path:
        print("No input file selected. Exiting.")
        return
    geojson_path = ask_output_geojson_path(
        initial_dir=os.path.dirname(csv_path),
        suggested_name=os.path.splitext(os.path.basename(csv_path))[0] + ".geojson",
    )
    if not geojson_path:
        print("No output file selected. Exiting.")
        return

    api_key = get_api_key()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Read CSV
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("Title") or "").strip()
            url = (row.get("URL") or "").strip()
            if not title and not url:
                continue
            rows.append(row)

    features = []
    request_count = 0
    limit_reached = False
    for i, row in enumerate(rows):
        title = (row.get("Title") or "").strip()
        url = (row.get("URL") or "").strip()
        print(f"Processing: {title or url or '(no title/url)'}")
        details = None
        if request_count < API_REQUEST_LIMIT:
            place_id = find_place_id(api_key, title, url)
            request_count += 1
            if place_id and request_count < API_REQUEST_LIMIT:
                time.sleep(REQUEST_DELAY_S)
                details = get_place_details(api_key, place_id)
                request_count += 1
            elif place_id:
                limit_reached = True
        else:
            limit_reached = True
        if not details and i > 0:
            time.sleep(REQUEST_DELAY_S)
        features.append(build_feature(row, details, now_iso))

    fc = {"type": "FeatureCollection", "features": features}
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(features)} features to {geojson_path}")
    if limit_reached:
        print(f"API request limit reached ({request_count} requests, max {API_REQUEST_LIMIT}). Remaining rows have no location data.")


if __name__ == "__main__":
    main()
