"""
Admin forms for the custom User model.

Django's `UserCreationForm`/`UserChangeForm` are written to be subclassed
this way (see the Django docs' "A full example" for custom user models):
overriding `Meta.model` and `Meta.fields` is enough, since the forms
otherwise operate generically through `self._meta.model`. Without this,
`django.contrib.auth.admin.UserAdmin`'s default forms assume a `username`
field, which our email-only User doesn't have.
"""

from django.contrib.auth.forms import UserChangeForm as DjangoUserChangeForm
from django.contrib.auth.forms import UserCreationForm as DjangoUserCreationForm

from identity.models import User


class UserCreationForm(DjangoUserCreationForm):
    class Meta(DjangoUserCreationForm.Meta):
        model = User
        fields = ('email', 'first_name', 'last_name', 'phone_number')


class UserChangeForm(DjangoUserChangeForm):
    class Meta(DjangoUserChangeForm.Meta):
        model = User
        fields = '__all__'
