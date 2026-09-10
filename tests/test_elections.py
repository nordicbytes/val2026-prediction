from valforecast.ingest.elections import district_id_2018


def test_2018_physical_district_id_is_eight_digits() -> None:
    assert district_id_2018(1, 14, 101, collection=False) == "01140101"


def test_2018_collection_district_id_is_six_digits() -> None:
    assert district_id_2018(1, 14, 0, collection=True) == "011400"
    assert district_id_2018(1, 80, 1, collection=True) == "018001"

