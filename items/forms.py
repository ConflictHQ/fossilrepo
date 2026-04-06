from django import forms

from .models import Item

tw = "w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm"


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["name", "description", "price", "sku", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": tw, "placeholder": "Item name"}),
            "description": forms.Textarea(attrs={"class": tw, "rows": 3, "placeholder": "Description"}),
            "price": forms.NumberInput(attrs={"class": tw, "step": "0.01", "placeholder": "0.00"}),
            "sku": forms.TextInput(attrs={"class": tw, "placeholder": "SKU-001"}),
            "is_active": forms.CheckboxInput(attrs={"class": "rounded border-gray-300 text-indigo-600"}),
        }
