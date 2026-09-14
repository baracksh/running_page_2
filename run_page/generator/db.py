import datetime
import json
import os
import random
import string
import time

from geopy.geocoders import options, Nominatim
from sqlalchemy import (
    Column,
    Float,
    Integer,
    Interval,
    String,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()


# random user name 8 letters
def randomword():
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(4))


options.default_user_agent = "running_page"
# reverse the location (lat, lon) -> location detail
g = Nominatim(user_agent=randomword())


# -----------------------------------------------------------------------------
# Reverse-geocode cache (Nominatim / OpenStreetMap).
#
# Nominatim's usage policy caps us at 1 request/second.  Calling it for every
# activity during bulk sync blows the timeout (hundreds of activities ⇒ 60+ min).
# Instead we memoise each (lat, lon) pair rounded to 3 decimals (~110 m) into a
# JSON file checked into git.  Future syncs only hit Nominatim for genuinely
# new start points (rare — most runners do loops from the same neighbourhood).
# -----------------------------------------------------------------------------
GEO_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "geo_cache.json"
)


def _load_geo_cache():
    if os.path.exists(GEO_CACHE_FILE):
        try:
            with open(GEO_CACHE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}
    return {}


def _save_geo_cache(cache):
    with open(GEO_CACHE_FILE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2, ensure_ascii=False, sort_keys=True)


def get_cached_country(start_point):
    """Return the reverse-geocoded country/region for (lat, lon), using the
    persistent cache.  Cache misses trigger a single Nominatim call and a
    1-second sleep to honour its rate limit."""
    if not start_point or len(start_point) < 2:
        return ""
    try:
        lat = float(start_point[0])
        lon = float(start_point[1])
    except (TypeError, ValueError):
        return ""
    key = f"{round(lat, 3)},{round(lon, 3)}"
    cache = _load_geo_cache()
    if key in cache:
        return cache[key]
    country = ""
    try:
        loc = g.reverse(f"{lat}, {lon}", language="zh")
        if loc:
            country = str(loc)
    except Exception as exc:  # noqa: BLE001 — Nominatim failures must not abort sync
        print(f"Nominatim reverse failed for {key}: {exc}")
    # Persist even an empty result so we don't hammer a failing endpoint.
    cache[key] = country
    _save_geo_cache(cache)
    time.sleep(1.0)
    return country


def lookup_cached_country(start_point):
    """Read-only lookup against the geo cache (no Nominatim calls, no sleeps).
    Returns "" on cache miss — used to backfill existing activities without
    triggering network traffic on every sync."""
    if not start_point or len(start_point) < 2:
        return ""
    try:
        lat = float(start_point[0])
        lon = float(start_point[1])
    except (TypeError, ValueError):
        return ""
    key = f"{round(lat, 3)},{round(lon, 3)}"
    return _load_geo_cache().get(key, "")


ACTIVITY_KEYS = [
    "run_id",
    "name",
    "distance",
    "moving_time",
    "type",
    "subtype",
    "start_date",
    "start_date_local",
    "location_country",
    "summary_polyline",
    "average_heartrate",
    "average_speed",
    "elevation_gain",
]


class Activity(Base):
    __tablename__ = "activities"

    run_id = Column(Integer, primary_key=True)
    name = Column(String)
    distance = Column(Float)
    moving_time = Column(Interval)
    elapsed_time = Column(Interval)
    type = Column(String)
    subtype = Column(String)
    start_date = Column(String)
    start_date_local = Column(String)
    location_country = Column(String)
    summary_polyline = Column(String)
    average_heartrate = Column(Float)
    average_speed = Column(Float)
    elevation_gain = Column(Float)
    streak = None

    def to_dict(self):
        out = {}
        for key in ACTIVITY_KEYS:
            attr = getattr(self, key)
            if isinstance(attr, (datetime.timedelta, datetime.datetime)):
                out[key] = str(attr)
            else:
                out[key] = attr

        if self.streak:
            out["streak"] = self.streak

        return out


def update_or_create_activity(session, run_activity):
    created = False
    try:
        activity = (
            session.query(Activity).filter_by(run_id=int(run_activity.id)).first()
        )

        current_elevation_gain = 0.0  # default value

        # https://github.com/stravalib/stravalib/blob/main/src/stravalib/strava_model.py#L639C1-L643C41
        if (
            hasattr(run_activity, "total_elevation_gain")
            and run_activity.total_elevation_gain is not None
        ):
            current_elevation_gain = float(run_activity.total_elevation_gain)
        elif (
            hasattr(run_activity, "elevation_gain")
            and run_activity.elevation_gain is not None
        ):
            current_elevation_gain = float(run_activity.elevation_gain)

        if not activity:
            start_point = run_activity.start_latlng
            location_country = getattr(run_activity, "location_country", "")
            # Reverse-geocode the start point via the persistent Nominatim cache.
            # Most syncs are cache hits, so we only hit the 1 req/sec endpoint for
            # genuinely new neighbourhoods.
            if not location_country and start_point:
                location_country = get_cached_country(start_point)

            activity = Activity(
                run_id=run_activity.id,
                name=run_activity.name,
                distance=run_activity.distance,
                moving_time=run_activity.moving_time,
                elapsed_time=run_activity.elapsed_time,
                type=run_activity.type,
                subtype=run_activity.subtype,
                start_date=run_activity.start_date,
                start_date_local=run_activity.start_date_local,
                location_country=location_country,
                average_heartrate=run_activity.average_heartrate,
                average_speed=float(run_activity.average_speed),
                elevation_gain=current_elevation_gain,
                summary_polyline=(
                    run_activity.map and run_activity.map.summary_polyline or ""
                ),
            )
            session.add(activity)
            created = True
        else:
            activity.name = run_activity.name
            activity.distance = float(run_activity.distance)
            activity.moving_time = run_activity.moving_time
            activity.elapsed_time = run_activity.elapsed_time
            activity.type = run_activity.type
            activity.subtype = run_activity.subtype
            activity.average_heartrate = run_activity.average_heartrate
            activity.average_speed = float(run_activity.average_speed)
            activity.elevation_gain = current_elevation_gain
            activity.summary_polyline = (
                run_activity.map and run_activity.map.summary_polyline or ""
            )
            # Backfill location_country from the persistent cache for activities
            # that pre-date the cache (e.g. the initial 532 COROS activities
            # committed before reverse geocoding was wired up).  This is a
            # cache-only lookup — no Nominatim traffic — so it's safe on every sync.
            if not activity.location_country:
                cached = lookup_cached_country(run_activity.start_latlng)
                if cached:
                    activity.location_country = cached
    except Exception as e:
        print(f"something wrong with {run_activity.id}")
        print(str(e))

    return created


def add_missing_columns(engine, model):
    inspector = inspect(engine)
    table_name = model.__tablename__
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    missing_columns = []

    for column in model.__table__.columns:
        if column.name not in columns:
            missing_columns.append(column)
    if missing_columns:
        with engine.connect() as conn:
            for column in missing_columns:
                column_type = str(column.type)
                conn.execute(
                    text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column.name} {column_type}"
                    )
                )


def init_db(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)

    # check missing columns
    add_missing_columns(engine, Activity)

    sm = sessionmaker(bind=engine)
    session = sm()
    # apply the changes
    session.commit()
    return session
