from stock_assistant.common.time import CHINA_TZ, china_now, utc_now


def test_time_helpers_return_aware_datetimes() -> None:
    assert utc_now().utcoffset() is not None
    assert china_now().tzinfo == CHINA_TZ
