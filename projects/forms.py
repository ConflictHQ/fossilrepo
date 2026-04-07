from django import forms

from organization.models import Team

from .models import Project, ProjectGroup, ProjectTeam

tw = "w-full rounded-md border-gray-300 shadow-sm focus:border-brand focus:ring-brand sm:text-sm"

REPO_SOURCE_CHOICES = [
    ("empty", "Create empty repository"),
    ("fossil_url", "Clone from Fossil URL"),
]


class ProjectGroupForm(forms.ModelForm):
    class Meta:
        model = ProjectGroup
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": tw, "placeholder": "Group name"}),
            "description": forms.Textarea(attrs={"class": tw, "rows": 3, "placeholder": "Description (optional)"}),
        }


class ProjectForm(forms.ModelForm):
    repo_source = forms.ChoiceField(
        choices=REPO_SOURCE_CHOICES,
        initial="empty",
        widget=forms.RadioSelect,
        required=False,
    )
    clone_url = forms.URLField(
        required=False,
        widget=forms.URLInput(attrs={"placeholder": "https://fossil-scm.org/home"}),
        help_text="Fossil repository URL to clone from",
    )

    class Meta:
        model = Project
        fields = ["name", "description", "visibility", "group"]
        widgets = {
            "name": forms.TextInput(attrs={"class": tw, "placeholder": "Project name"}),
            "description": forms.Textarea(attrs={"class": tw, "rows": 3, "placeholder": "Description"}),
            "visibility": forms.Select(attrs={"class": tw}),
            "group": forms.Select(attrs={"class": tw}),
        }

    def clean(self):
        cleaned = super().clean()
        repo_source = cleaned.get("repo_source", "empty")
        clone_url = cleaned.get("clone_url", "").strip()
        if repo_source == "fossil_url" and not clone_url:
            self.add_error("clone_url", "Clone URL is required when cloning from a Fossil URL.")
        return cleaned


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
