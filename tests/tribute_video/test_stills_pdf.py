import pytest
from PIL import Image

from flashback.tribute_video.stills_pdf import assemble_pdf_from_stills


def _png(path, color):
    Image.new("RGB", (896, 1600), color).save(path)
    return str(path)


def test_pdf_and_poster_from_stills(tmp_path):
    stills = [_png(tmp_path / "0.png", (200, 180, 140)),
              _png(tmp_path / "1.png", (100, 90, 70)),
              _png(tmp_path / "2.png", (50, 40, 30))]
    pdf = tmp_path / "out.pdf"
    poster = tmp_path / "poster.jpg"
    n = assemble_pdf_from_stills(stills, str(pdf), str(poster))
    assert n == 3
    assert pdf.exists() and pdf.stat().st_size > 0
    assert poster.exists()
    with Image.open(poster) as im:
        assert im.format == "JPEG"


def test_empty_raises(tmp_path):
    with pytest.raises(ValueError):
        assemble_pdf_from_stills([], str(tmp_path / "x.pdf"))
