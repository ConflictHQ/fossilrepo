from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Organization, Team

tw = "w-full rounded-md border-gray-300 shadow-sm focus:border-brand focus:ring-brand sm:text-sm"


class OrganizationSettingsForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name", "description", "website"]
        widgets = {
            "name": forms.TextInput(attrs={"class": tw, "placeholder": "Organization name"}),
            "description": forms.Textarea(attrs={"class": tw, "rows": 3, "placeholder": "Description"}),
            "website": forms.URLInput(attrs={"class": tw, "placeholder": "https://example.com"}),
        }


class MemberAddForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": tw}),
        label="User",
    )

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        if org:
            existing_member_ids = org.members.filter(deleted_at__isnull=True).values_list("member_id", flat=True)
            self.fields["user"].queryset = User.objects.filter(is_active=True).exclude(id__in=existing_member_ids)


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": tw, "placeholder": "Team name"}),
            "description": forms.Textarea(attrs={"class": tw, "rows": 3, "placeholder": "Description"}),
        }


class TeamMemberAddForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": tw}),
        label="User",
    )

    def __init__(self, *args, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        if team:
            existing_member_ids = team.members.values_list("id", flat=True)
            self.fields["user"].queryset = User.objects.filter(is_active=True).exclude(id__in=existing_member_ids)


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": tw, "placeholder": "Password"}),
        strip=False,
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={"class": tw, "placeholder": "Confirm password"}),
        strip=False,
    )

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name"]
        widgets = {
            "username": forms.TextInput(attrs={"class": tw, "placeholder": "Username"}),
            "email": forms.EmailInput(attrs={"class": tw, "placeholder": "email@example.com"}),
            "first_name": forms.TextInput(attrs={"class": tw, "placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"class": tw, "placeholder": "Last name"}),
        }

    def clean_password1(self):
        password = self.cleaned_data.get("password1")
        try:
            validate_password(password)
        except ValidationError as e:
            raise ValidationError(e.messages) from None
        return password

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("password1")
        p2 = cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "is_active", "is_staff"]
        widgets = {
            "email": forms.EmailInput(attrs={"class": tw, "placeholder": "email@example.com"}),
            "first_name": forms.TextInput(attrs={"class": tw, "placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"class": tw, "placeholder": "Last name"}),
            "is_active": forms.CheckboxInput(attrs={"class": "rounded border-gray-300 text-brand focus:ring-brand"}),
            "is_staff": forms.CheckboxInput(attrs={"class": "rounded border-gray-300 text-brand focus:ring-brand"}),
        }

    def __init__(self, *args, editing_self=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing_self:
            # Prevent self-lockout: cannot toggle own is_active
            self.fields["is_active"].disabled = True
            self.fields["is_active"].help_text = "You cannot deactivate your own account."


class UserPasswordForm(forms.Form):
    new_password1 = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={"class": tw, "placeholder": "New password"}),
        strip=False,
    )
    new_password2 = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(attrs={"class": tw, "placeholder": "Confirm new password"}),
        strip=False,
    )

    def clean_new_password1(self):
        password = self.cleaned_data.get("new_password1")
        try:
            validate_password(password)
        except ValidationError as e:
            raise ValidationError(e.messages) from None
        return password

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("new_password1")
        p2 = cleaned_data.get("new_password2")
        if p1 and p2 and p1 != p2:
            self.add_error("new_password2", "Passwords do not match.")
        return cleaned_data
