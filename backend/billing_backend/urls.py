from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url="/static/img/logo.ico", permanent=True)),
    path("admin/", admin.site.urls),
    path("", include("billing.urls")),
]
