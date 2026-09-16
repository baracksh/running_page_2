"""One-off helper: pre-fill run_page/generator/geo_cache.json by calling
Nominatim for every distinct start point in activities.json.  Run this
locally once; commit the resulting geo_cache.json so the production
workflow can stay well below its 1 req/sec budget.

Usage (from project root):

    ./.venv/bin/python run_page/generator/_prefill_geo_cache.py

Honours Nominatim's usage policy by sleeping 1 second between requests.
"""

import json
import os
import sys
import time
from collections import Counter

# Make `generator` importable when running this script directly.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import polyline  # noqa: E402

from generator.db import (
    GEO_CACHE_FILE,
    _load_geo_cache,
    _save_geo_cache,
    g,
)  # noqa: E402

ACTIVITIES_JSON = os.path.join(
    HERE, os.pardir, os.pardir, "src", "static", "activities.json"
)


def main():
    with open(ACTIVITIES_JSON, "r", encoding="utf-8") as fh:
        activities = json.load(fh)

    keys = []
    for a in activities:
        pl = a.get("summary_polyline", "")
        if not pl:
            continue
        try:
            pts = polyline.decode(pl)
        except Exception:
            continue
        if not pts:
            continue
        lat, lon = pts[0]
        keys.append(f"{round(lat, 3)},{round(lon, 3)}")

    unique = sorted(set(keys))
    print(
        f"{len(activities)} activities, {len(keys)} start points, {len(unique)} unique"
    )

    cache = _load_geo_cache()
    missing = [k for k in unique if k not in cache]
    print(f"Cache hits: {len(unique) - len(missing)}, misses: {len(missing)}")

    if not missing:
        print("geo_cache.json already complete; nothing to do.")
        return

    print(f"Filling {len(missing)} cache entries (~{len(missing)} s wall time)…")
    for i, key in enumerate(missing, 1):
        lat_s, lon_s = key.split(",")
        try:
            loc = g.reverse(f"{lat_s}, {lon_s}", language="zh")
            cache[key] = str(loc) if loc else ""
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(missing)}] {key}  FAIL: {exc}")
            cache[key] = ""
        # Persist after every entry so a crash doesn't lose progress.
        _save_geo_cache(cache)
        if i < len(missing):
            time.sleep(1.0)
    print(f"Done.  Wrote {GEO_CACHE_FILE}.")


if __name__ == "__main__":
    main()
