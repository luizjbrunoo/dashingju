from django.shortcuts import render


def home(request):
    """Página inicial do site."""
    return render(request, "home.html")
