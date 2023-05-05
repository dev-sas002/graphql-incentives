from django.urls import path
from . import views

urlpatterns = [
    path("plot/", views.plot_view, name="plot_view"),
    path("", views.home_view, name="home"),
]
