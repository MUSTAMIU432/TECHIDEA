"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path

from administration.views import admin_download_attachment_view
from config.views import health
from graphql_api.views import graphql_view
from ideas.views import download_attachment_view, upload_attachment_view
from identity.views import avatar_view, user_avatar_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health, name='health'),
    path('graphql/', graphql_view, name='graphql'),
    # Attachment binary transfer (S2-007) - the one HTTP surface for Ideas
    # beyond GraphQL, and only for the bytes themselves. See
    # `ideas/views.py`'s module docstring for why these two exist outside
    # GraphQL and everything else about an attachment does not.
    path(
        'ideas/<int:idea_id>/attachments/',
        upload_attachment_view,
        name='idea-attachment-upload',
    ),
    path(
        'ideas/<int:idea_id>/attachments/<int:attachment_id>/download/',
        download_attachment_view,
        name='idea-attachment-download',
    ),
    # Profile photos: bytes only, like attachments. See `identity/views.py`.
    path('account/avatar/', avatar_view, name='account-avatar'),
    path('users/<int:user_id>/avatar/', user_avatar_view, name='user-avatar'),
    # The administration console's evidence download: a separate endpoint
    # with its own platform-permission check and an audit record per
    # download, sharing the safe-download response with the one above. See
    # `administration/views.py`.
    path(
        'administration/attachments/<int:attachment_id>/download/',
        admin_download_attachment_view,
        name='admin-attachment-download',
    ),
]
