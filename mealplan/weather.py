"""Seven-day forecast from Open-Meteo, plus the one number the planner wants.

Open-Meteo is free and needs no API key. The place name in config.json is
resolved to coordinates once through their geocoding endpoint and cached, so a
typo in the town name shows up as a clear error on first run rather than as
weather for somewhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 20

# WMO weather codes -> (label, emoji). Only the groups we care about.
WMO = {
    0: ("Clear", "\u2600\ufe0f"), 1: ("Mainly clear", "\U0001f324\ufe0f"),
    2: ("Partly cloudy", "\u26c5"), 3: ("Overcast", "\u2601\ufe0f"),
    45: ("Fog", "\U0001f32b\ufe0f"), 48: ("Rime fog", "\U0001f32b\ufe0f"),
    51: ("Light drizzle", "\U0001f327\ufe0f"), 53: ("Drizzle", "\U0001f327\ufe0f"),
    55: ("Heavy drizzle", "\U0001f327\ufe0f"),
    61: ("Light rain", "\U0001f327\ufe0f"), 63: ("Rain", "\U0001f327\ufe0f"),
    65: ("Heavy rain", "\U0001f326\ufe0f"),
    66: ("Freezing rain", "\U0001f328\ufe0f"), 67: ("Freezing rain", "\U0001f328\ufe0f"),
    71: ("Light snow", "\u2744\ufe0f"), 73: ("Snow", "\u2744\ufe0f"),
    75: ("Heavy snow", "\u2744\ufe0f"), 77: ("Snow grains", "\u2744\ufe0f"),
    80: ("Showers", "\U0001f326\ufe0f"), 81: ("Showers", "\U0001f326\ufe0f"),
    82: ("Violent showers", "\u26c8\ufe0f"),
    85: ("Snow showers", "\U0001f328\ufe0f"), 86: ("Snow showers", "\U0001f328\ufe0f"),
    95: ("Thunderstorm", "\u26c8\ufe0f"), 96: ("Thunderstorm", "\u26c8\ufe0f"),
    99: ("Thunderstorm", "\u26c8\ufe0f"),
}


class WeatherError(RuntimeError):
    pass


@dataclass
class DayWeather:
    day: date
    temp_max: float
    temp_min: float
    precip_mm: float
    precip_chance: int
    wind_kmh: float
    code: int

    @property
    def label(self) -> str:
        return WMO.get(self.code, ("Unknown", "\u2753"))[0]

    @property
    def icon(self) -> str:
        return WMO.get(self.code, ("Unknown", "\u2753"))[1]

    @property
    def is_wet(self) -> bool:
        return self.precip_mm >= 1.0 or self.precip_chance >= 50

    def to_json(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(), "temp_max": self.temp_max,
            "temp_min": self.temp_min, "precip_mm": self.precip_mm,
            "precip_chance": self.precip_chance, "wind_kmh": self.wind_kmh,
            "code": self.code,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "DayWeather":
        return cls(
            day=date.fromisoformat(d["day"]), temp_max=float(d["temp_max"]),
            temp_min=float(d["temp_min"]), precip_mm=float(d["precip_mm"]),
            precip_chance=int(d["precip_chance"]), wind_kmh=float(d["wind_kmh"]),
            code=int(d["code"]),
        )


def geocode(place: str) -> tuple[float, float, str]:
    """Resolve a place name to (lat, lon, pretty name)."""
    r = requests.get(
        GEOCODE_URL,
        params={"name": place, "count": 1, "language": "en", "format": "json"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    results = (r.json() or {}).get("results") or []
    if not results:
        raise WeatherError(
            f"Could not find a place called {place!r}. Put a town or city "
            f"(optionally 'Town, CC') in config.json under \"location\"."
        )
    top = results[0]
    bits = [top.get("name"), top.get("admin1"), top.get("country_code")]
    pretty = ", ".join(b for b in bits if b)
    return float(top["latitude"]), float(top["longitude"]), pretty


def forecast(lat: float, lon: float, days: int = 7) -> list[DayWeather]:
    """Daily forecast starting today, in the location's own timezone."""
    r = requests.get(
        FORECAST_URL,
        params={
            "latitude": lat, "longitude": lon,
            "daily": ",".join([
                "weather_code", "temperature_2m_max", "temperature_2m_min",
                "precipitation_sum", "precipitation_probability_max",
                "wind_speed_10m_max",
            ]),
            "timezone": "auto",
            "forecast_days": max(1, min(days, 16)),
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    d = (r.json() or {}).get("daily") or {}
    if not d.get("time"):
        raise WeatherError("Open-Meteo returned no daily forecast.")

    def col(key: str, default: float = 0.0) -> list[float]:
        vals = d.get(key) or []
        return [default if v is None else float(v) for v in vals]

    out: list[DayWeather] = []
    codes = col("weather_code")
    tmax, tmin = col("temperature_2m_max"), col("temperature_2m_min")
    psum, pprob = col("precipitation_sum"), col("precipitation_probability_max")
    wind = col("wind_speed_10m_max")
    for i, iso in enumerate(d["time"]):
        out.append(DayWeather(
            day=datetime.fromisoformat(iso).date(),
            temp_max=tmax[i] if i < len(tmax) else 15.0,
            temp_min=tmin[i] if i < len(tmin) else 10.0,
            precip_mm=psum[i] if i < len(psum) else 0.0,
            precip_chance=int(pprob[i]) if i < len(pprob) else 0,
            wind_kmh=wind[i] if i < len(wind) else 0.0,
            code=int(codes[i]) if i < len(codes) else 3,
        ))
    return out


# --- the bit the planner actually consumes -----------------------------------

# Anchor points for "how hearty should dinner be", against the day's high.
# Read as (degrees C, wanted heartiness 0..1); everything between is linear.
_TEMP_CURVE = [
    (30.0, 0.05),
    (24.0, 0.20),
    (19.0, 0.38),
    (14.0, 0.58),
    (9.0, 0.75),
    (3.0, 0.90),
    (-5.0, 1.00),
]


def _interp(x: float, curve: list[tuple[float, float]]) -> float:
    """Piecewise-linear lookup down a curve sorted high-x to low-x."""
    if x >= curve[0][0]:
        return curve[0][1]
    if x <= curve[-1][0]:
        return curve[-1][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x1 <= x <= x0:
            t = (x - x1) / (x0 - x1) if x0 != x1 else 0.0
            return y1 + t * (y0 - y1)
    return curve[-1][1]


def wanted_heartiness(w: DayWeather) -> float:
    """0 = light and fresh, 1 = a stew you eat with a spoon.

    Temperature does most of the work; rain and wind nudge it up, because a wet
    evening wants comfort food at a temperature a dry one would not.
    """
    score = _interp(w.temp_max, _TEMP_CURVE)
    if w.precip_mm >= 5.0 or w.precip_chance >= 80:
        score += 0.15
    elif w.is_wet:
        score += 0.09
    if w.wind_kmh >= 40:
        score += 0.08
    elif w.wind_kmh >= 25:
        score += 0.04
    return max(0.0, min(1.0, score))


def describe(w: DayWeather) -> str:
    """One short phrase explaining what the weather asked for."""
    want = wanted_heartiness(w)
    if want >= 0.75:
        mood = "wants something hearty"
    elif want >= 0.55:
        mood = "wants something substantial"
    elif want >= 0.35:
        mood = "middle of the road"
    else:
        mood = "wants something light"
    wet = " and wet" if w.is_wet else ""
    return f"{w.temp_max:.0f}\u00b0C{wet}, {mood}"
