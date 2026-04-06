from django import forms

from organization.models import Team

from .models import Project, ProjectTeam

tw = "w-full rounded-md border-gray-300 shadow-sm focus:border-brand focus:ring-brand sm:text-sm"


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description", "visibility"]
        widgets = {
            "name": forms.TextInput(attrs={"class": tw, "placeholder": "Project name"}),
            "description": forms.Textarea(attrs={"class": tw, "rows": 3, "placeholder": "Description"}),
            "visibility": forms.Select(attrs={"class": tw}),
        }


class ProjectTeamAddForm(forms.Form):
    team = forms.ModelChoiceField(
        queryset=Team.objects.none(),
        widget=forms.Select(attrs={"class": tw}),
        label="Team",
    )
    role = forms.ChoiceField(
        choices=ProjectTeam.Role.choices,
        widget=forms.Select(attrs={"class": tw}),
        label="Role",
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project:
            assigned_team_ids = project.project_teams.filter(deleted_at__isnull=True).values_list("team_id", flat=True)
            self.fields["team"].queryset = Team.objects.filter(organization=project.organization, deleted_at__isnull=True).exclude(
                id__in=assigned_team_ids
            )


class ProjectTeamEditForm(forms.Form):
    role = forms.ChoiceField(
        choices=ProjectTeam.Role.choices,
        widget=forms.Select(attrs={"class": tw}),
        label="Role",
    )
