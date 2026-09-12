from valforecast.seats.method import allocate_hamilton_seats, allocate_modified_sainte_lague


def test_modified_sainte_lague_matches_valmyndigheten_example_3() -> None:
    """Example 3 in Valmyndigheten's mandate manual, first four seats.

    Votes and the first four awards are taken from the published walkthrough
    (S, S, M, C). Seats 5–8 are the same algorithm continued by hand.
    """
    votes = {
        "M": 15_200,
        "C": 11_746,
        "L": 10_538,
        "KD": 11_695,
        "S": 43_696,
        "V": 7_864,
        "MP": 4_354,
        "SD": 11_250,
    }
    first_four = allocate_modified_sainte_lague(votes, 4)
    assert first_four == {
        "S": 2,
        "M": 1,
        "C": 1,
        "L": 0,
        "KD": 0,
        "V": 0,
        "MP": 0,
        "SD": 0,
    }
    first_eight = allocate_modified_sainte_lague(votes, 8)
    assert first_eight == {
        "S": 3,
        "M": 1,
        "C": 1,
        "KD": 1,
        "SD": 1,
        "L": 1,
        "V": 0,
        "MP": 0,
    }


def test_hamilton_matches_valmyndigheten_example_2() -> None:
    eligible = {
        "Valkoping V": 15_927,
        "Valkoping C": 15_886,
        "Valkoping O": 17_044,
    }
    seats = allocate_hamilton_seats(eligible, 67)
    assert seats == {
        "Valkoping V": 22,
        "Valkoping C": 22,
        "Valkoping O": 23,
    }
    assert sum(seats.values()) == 67


def test_hamilton_tie_goes_to_first_unit_id() -> None:
    eligible = {"B": 50, "A": 50, "C": 50}
    seats = allocate_hamilton_seats(eligible, 4)
    assert seats == {"A": 2, "B": 1, "C": 1}


def test_sainte_lague_tie_goes_to_first_party_code() -> None:
    seats = allocate_modified_sainte_lague({"M": 120, "C": 120}, 1)
    assert seats == {"C": 1, "M": 0}
