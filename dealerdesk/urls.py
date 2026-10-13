from django.contrib import admin
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("", core_views.portal_view, name="portal"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
    # Add your apps' URLs here, e.g. path("api/", include("orders.urls")),
]
