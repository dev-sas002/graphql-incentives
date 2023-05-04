from django.urls import path

from incentives import views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("plot/", views.plot_view, name="plot_view"),
    path("plot/chart.png", views.chart_image_view, name="chart_image"),
    path("healthz", views.healthz, name="healthz"),
]
