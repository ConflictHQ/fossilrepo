from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .forms import LoginForm


@ratelimit(key="ip", rate="10/m", block=True)
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            next_url = request.GET.get("next", "dashboard")
            return redirect(next_url)
    else:
        form = LoginForm()

    return render(request, "auth1/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("auth1:login")
