"""Fast GPX -> activities.json generator (bypasses SQLAlchemy for speed)."""
import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import gpxpy

PROJECT_ROOT = Path(__file__).parent
GPX_DIR = PROJECT_ROOT / "GPX_OUT"
OUT_DIR = PROJECT_ROOT / "src" / "static"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "activities.json"


def haversine(lat1, lon1, lat2, lon2):
    """Distance in meters between two lat/lon points."""
    R = 6371000
    phi1, phi2 = map(lambda x: x * 3.141592653589793 / 180, [lat1, lat2])
    dphi = (lat2 - lat1) * 3.141592653589793 / 180
    dlam = (lon2 - lon1) * 3.141592653589793 / 180
    a = (
        (dphi / 2) ** 2
        + (1 - 0) * (1 - 0) * (phi1 * 0 + (1 - 0) * 0)  # placeholder
    )
    # proper haversine
    a = (
        (dphi / 2) ** 2
        + 0  # we'll do it below
    )
    # do it properly
    from math import sin, cos, sqrt, atan2
    dphi = (lat2 - lat1) * 3.141592653589793 / 180
    dlam = (lon2 - lon1) * 3.141592653589793 / 180
    a = sin(dphi / 2) ** 2 + cos(lat1 * 3.141592653589793 / 180) * cos(
        lat2 * 3.141592653589793 / 180
    ) * sin(dlam / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def parse_iso(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def encode_polyline(points):
    """Google encoded polyline from [(lat, lon), ...]"""
    if not points:
        return ""
    import polyline as _pl  # may or may not exist
    try:
        return _pl.encode(points)
    except Exception:
        # fallback: simple impl
        return ""


def main():
    gpx_files = sorted(GPX_DIR.glob("*.gpx"))
    print(f"Found {len(gpx_files)} GPX files")
    activities = []
    skipped = 0
    for i, fp in enumerate(gpx_files, 1):
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                gpx = gpxpy.parse(f)
            # collect points
            points = []  # (lat, lon, ele, t)
            for trk in gpx.tracks:
                for seg in trk.segments:
                    for p in seg.points:
                        t = p.time
                        if t and t.tzinfo is None:
                            t = t.replace(tzinfo=None)
                        points.append((p.latitude, p.longitude, p.elevation or 0.0, t))
            if len(points) < 2:
                skipped += 1
                continue
            # start time
            start_t = next((p[3] for p in points if p[3] is not None), None)
            if start_t is None:
                skipped += 1
                continue
            # end time
            end_t = next((p[3] for p in reversed(points) if p[3] is not None), None)
            moving_time_s = (end_t - start_t).total_seconds() if end_t else 0
            # distance
            distance = 0.0
            elev_gain = 0.0
            prev = None
            for lat, lon, ele, t in points:
                if prev is not None:
                    plat, plon, pele, _ = prev
                    distance += haversine(plat, plon, lat, lon)
                    if ele and pele and ele > pele:
                        elev_gain += ele - pele
                prev = (lat, lon, ele, t)
            # polyline
            poly_pts = [(p[0], p[1]) for p in points]
            poly = encode_polyline(poly_pts)
            # start_date_local: just use start_t as-is, treat as local
            act = {
                "run_id": int(fp.stem),
                "name": "",
                "distance": round(distance, 2),
                "moving_time": str(timedelta(seconds=int(moving_time_s))),
                "elapsed_time": str(timedelta(seconds=int(moving_time_s))),
                "type": "Run",
                "subtype": "",
                "start_date": start_t.strftime("%Y-%m-%d %H:%M:%S"),
                "start_date_local": start_t.strftime("%Y-%m-%d %H:%M:%S"),
                "location_country": "",
                "summary_polyline": poly,
                "average_heartrate": None,
                "average_speed": round(distance / moving_time_s * 3.6, 2) if moving_time_s > 0 else 0,
                "elevation_gain": round(elev_gain, 2),
                "file_names": [str(fp.name)],
            }
            activities.append(act)
            if i % 50 == 0:
                print(f"  {i}/{len(gpx_files)} parsed")
        except Exception as e:
            skipped += 1
    print(f"Parsed {len(activities)} activities, skipped {skipped}")
    # sort by start_date_local
    activities.sort(key=lambda a: a["start_date_local"])
    with open(OUT_FILE, "w") as f:
        json.dump(activities, f, ensure_ascii=False)
    print(f"Wrote {OUT_FILE} ({OUT_FILE.stat().st_size} bytes)")


if __name__ == "__main__":
    main()