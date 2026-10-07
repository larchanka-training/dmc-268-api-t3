from dataclasses import replace

from sqlalchemy.engine import make_url

from app.config import load_settings


def test_database_url_escapes_special_characters_in_credentials():
    settings = replace(load_settings(), postgres_user="app@team", postgres_password="p@ss:w/rd%")

    url = make_url(settings.database_url)

    assert url.username == "app@team"
    assert url.password == "p@ss:w/rd%"
    assert url.host == settings.postgres_host
    assert url.database == settings.postgres_db
