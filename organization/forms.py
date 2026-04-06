from django import forms
from django.contrib.auth.models import User

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
