from django.apps import apps

from ideas.apps import IdeasConfig


def test_the_installed_app_uses_the_ideas_config():
    # Django picks this up because it is the only AppConfig in the app
    # (apps.py), so `INSTALLED_APPS` can stay a bare 'ideas' like every other
    # domain app in the project.
    assert isinstance(apps.get_app_config('ideas'), IdeasConfig)
