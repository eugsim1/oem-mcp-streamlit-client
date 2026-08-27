import pytest

from scripts.release_notes import newest_section


def test_newest_release_is_first_section() -> None:
    version, notes = newest_section("# Changelog\n\n## 2.0.0 - today\n\n- New\n\n## 1.0.0\n\n- Old\n")
    assert version == "2.0.0"
    assert notes == "- New"


def test_release_section_is_required() -> None:
    with pytest.raises(ValueError, match="no '## <version>'"):
        newest_section("# Changelog\n")
