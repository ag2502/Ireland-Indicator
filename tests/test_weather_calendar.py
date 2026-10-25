from datetime import date

import pandas as pd

from ingest import calendar_ie, weather


def test_open_meteo_rows_skip_missing_hours():
    payload = {
        "hourly": {
            "time": [0, 3600],
            "temperature_2m": [9.5, None],
            "precipitation": [0.4, None],
            "wind_speed_10m": [12.0, None],
        }
    }
    assert weather._rows(payload, "forecast") == [
        {"hour_at": 0, "temp_c": 9.5, "precip_mm": 0.4, "wind_kmh": 12.0, "source": "forecast"}
    ]


def test_calendar_flags():
    assert calendar_ie.is_bank_holiday(date(2026, 10, 26))
    assert calendar_ie.is_school_holiday(date(2026, 10, 28))
    assert not calendar_ie.is_school_holiday(date(2026, 10, 31))  # Saturday
    f = calendar_ie.flags(pd.Series([date(2026, 12, 25), date(2026, 11, 4)]))
    assert f["bank_holiday"].tolist() == [1, 0]
    assert f["school_holiday"].tolist() == [1, 0]
