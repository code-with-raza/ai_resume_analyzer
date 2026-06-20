from django.urls import path
from . import views
from django.views.generic import RedirectView  

# Routing endpoints specific to the analyzer application features
urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('upgrade-cv/', views.upgrade_cv_view, name='upgrade_cv'),
    path('', RedirectView.as_view(url='/login/', permanent=False)),
]