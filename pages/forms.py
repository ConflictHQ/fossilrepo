from django import forms

from .models import Page

tw = "w-full rounded-md border-gray-300 shadow-sm focus:border-brand focus:ring-brand sm:text-sm"


class PageForm(forms.ModelForm):
    class Meta:
        model = Page
        fields = ["name", "content", "is_published"]
        widgets = {
            "name": forms.TextInput(attrs={"class": tw, "placeholder": "Page title"}),
            "content": forms.Textarea(attrs={"class": tw + " font-mono", "rows": 20, "placeholder": "Write in Markdown..."}),
            "is_published": forms.CheckboxInput(attrs={"class": "rounded border-gray-300 text-brand"}),
        }
