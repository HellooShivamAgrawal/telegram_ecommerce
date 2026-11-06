import pytest
from io import BytesIO
from PIL import Image
from telegram_ecommerce.miniapp import images
from telegram_ecommerce.miniapp.links import mini_app_url


def test_thumbnail_is_small_and_preserves_original(tmp_path, monkeypatch):
    monkeypatch.setattr(images, 'ROOT', tmp_path)
    path = tmp_path / 'photo.png'
    Image.effect_noise((1800, 1200), 80).save(path)
    original = path.read_bytes()
    data = images.thumbnail('photo.png')
    with Image.open(BytesIO(data)) as result:
        assert max(result.size) == 720
        assert result.format == 'JPEG'
    assert len(data) < len(original) / 4
    assert path.read_bytes() == original
    with pytest.raises(FileNotFoundError):
        images.thumbnail('../outside.jpg')


def test_https_links_escape_query(monkeypatch):
    monkeypatch.setenv('MINI_APP_URL', 'https://shop.example')
    assert mini_app_url(q='blue & red') == 'https://shop.example/?q=blue+%26+red'
    for url in ('http://localhost', 'https://user:secret@example.com', 'https://shop.example/?token=secret'):
        monkeypatch.setenv('MINI_APP_URL', url)
        assert not mini_app_url()


def test_transparency_is_composited_on_light_background(tmp_path, monkeypatch):
    monkeypatch.setattr(images, 'ROOT', tmp_path)
    Image.new('RGBA', (20, 20), (0, 0, 0, 0)).save(tmp_path / 'transparent.png')
    with Image.open(BytesIO(images.thumbnail('transparent.png'))) as result:
        assert min(result.getpixel((5, 5))) > 220
