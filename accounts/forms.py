from django import forms
from django.contrib.auth.forms import AuthenticationForm


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "class": "w-full rounded-md border-gray-300 shadow-sm focus:border-brand focus:ring-brand",
                "placeholder": "Username",
                "autofocus": True,
            }
        )
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "w-full rounded-md border-gray-300 shadow-sm focus:border-brand focus:ring-brand",
                "placeholder": "Password",
            }
        )
    )
