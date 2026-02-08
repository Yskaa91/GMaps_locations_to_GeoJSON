# GMaps_locations_to_GeoJSON
Reads .csv, enrich each row with full address and GPS via Google Places API, and write a GeoJSON file. 
Primary intention was to enable complete migration from GMaps to Here WeGo.

Requires GOOGLE_PLACES_API_KEY set as an environment variable, with Places API and Geocoding API enabled.

Max requests per run is by default limited to 1000 to prevent exceeding the free use limit of the Google API.

*Use at your own risk*

# Environment
Tested with Python 3.12 and requests 2.32.5