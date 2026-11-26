# Texas ZIP code centre points

`tx_zip_centroids.csv` is what `transportation/distance.py` reads to estimate
one-way miles from two ZIP codes (ADR 0049). It holds no rider data: it is a
public table of places.

- **Source:** US Census Bureau, Gazetteer Files, ZIP Code Tabulation Areas
  (ZCTAs), national file. Public domain.
  - Page: https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
  - File: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_zcta_national.zip
    (inside it: `2026_Gaz_zcta_national.txt`)
- **Vintage:** 2026 (downloaded 2026-11-26; the national file has 33,791 ZCTAs).
- **Rows:** 1,989 Texas ZIP codes, plus the header line.
- **Columns:** `zip,lat,lon`. `lat` and `lon` are the Census "internal point"
  of the ZIP area (`INTPTLAT`, `INTPTLONG`), copied exactly as published, in
  decimal degrees. Sorted by ZIP.
- **Which ZIPs are kept:** the Texas ranges 73301, 73344, 75001-79999 and
  88510-88595. Only ZIPs that are an area on the map have a row. ZIPs used
  only for PO boxes or one large mailer (73301, 73344 and the 885 range among
  them) are not in the Census file, so they have no row and get no estimate.

## How to regenerate

1. Download the newest `<year>_Gaz_zcta_national.zip` from the Gazetteer
   Files page above (section "ZIP Code Tabulation Areas").
2. Read the text file inside it. The 2026 file is pipe-separated with the
   columns `GEOID|GEOIDFQ|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG`;
   earlier years are tab-separated and have no `GEOIDFQ`.
3. Keep the rows whose `GEOID` is in the Texas ranges, write `GEOID`,
   `INTPTLAT`, `INTPTLONG` as `zip,lat,lon`, sorted by ZIP, with Unix line
   endings.
4. Update the year, the file link and the row count in this file, and run
   `manage.py test transportation.test_distance`.

Never edit the coordinates by hand and never fill a missing ZIP with a guess:
a ZIP that is not in the table simply has no estimate, and staff read the
miles off the map.
