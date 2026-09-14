from importlib.resources import files


def test_desktop_logo_is_an_installable_resource():
    assert files('lightss_assets').joinpath('wled-logo.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
