import datetime, urllib

from django import dispatch, shortcuts
from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import views as auth_views
from django.core import exceptions, urlresolvers
from django.views import generic as generic_views
from django.views.generic import simple, edit as edit_views
from django.utils.translation import ugettext_lazy as _

from pushserver import signals
from pushserver.utils import updates

from mongogeneric import detail

from piplmesh.account import forms, models

HOME_CHANNEL_ID = 'home'

class FacebookLoginView(generic_views.RedirectView):
    """ 
    This view authenticates the user via Facebook.
    """

    permanent = False

    def get_redirect_url(self, **kwargs):
        args = {
            'client_id': settings.FACEBOOK_APP_ID,
            'scope': settings.FACEBOOK_SCOPE,
            'redirect_uri': self.request.build_absolute_uri(urlresolvers.reverse('facebook_callback')),
        }
        return "https://www.facebook.com/dialog/oauth?%(args)s" % {'args': urllib.urlencode(args)}

class FacebookCallbackView(generic_views.RedirectView):
    """ 
    Authentication callback. Redirects user to LOGIN_REDIRECT_URL. 
    """

    permanent = False
    # TODO: Redirect users to the page they initially came from
    url = settings.FACEBOOK_LOGIN_REDIRECT

    def get(self, request, *args, **kwargs):
        if 'code' in request.GET:
            # TODO: Add security measures to prevent attackers from sending a redirect to this url with a forged 'code'
            user = auth.authenticate(token=request.GET['code'], request=request)
            auth.login(request, user)
            return super(FacebookCallbackView, self).get(request, *args, **kwargs)
        else:
            # TODO: Message user that they have not been logged in because they cancelled the facebook app
            # TODO: Use information provided from facebook as to why the login was not successful
            return super(FacebookCallbackView, self).get(request, *args, **kwargs)

def logout(request):
    """
    After user logouts, redirect her back to the page she came from.
    """
    
    if request.method == 'POST':
        url = request.POST.get(auth.REDIRECT_FIELD_NAME)
        return auth_views.logout_then_login(request, url)
    else:
        raise exceptions.PermissionDenied

class UserView(detail.DetailView):
    """
    This view checks if user exist in database and returns his user page (profile).
    """

    template_name = 'user/user.html'
    document = models.User
    slug_field = 'username'
    slug_url_kwarg = 'username'

class RegistrationView(edit_views.FormView):
    """
    This view checks if form data are valid, saves new user.

    New user is authenticated, logged in and redirected to home page.
    """

    template_name = 'registration/registration.html'
    # TODO: Redirect users to the page they initially came from
    success_url = urlresolvers.reverse_lazy('home')
    form_class = forms.RegistrationForm

    def form_valid(self, form):
        new_user = models.User(
            username=form.cleaned_data['username'],
            first_name=form.cleaned_data['first_name'],
            last_name=form.cleaned_data['last_name'],
            email=form.cleaned_data['email'],
            gender=form.cleaned_data['gender'],
            birthdate=form.cleaned_data['birthdate'],
        )
        new_user.set_password(form.cleaned_data['password2'])
        new_user.save()
        # We update user with authentication data
        newuser = auth.authenticate(username=form.cleaned_data['username'], password=form.cleaned_data['password2'])
        assert newuser is not None, form.cleaned_data['username']
        auth.login(self.request, newuser)
        messages.success(self.request, _("Your registration was successful."))
        return super(RegistrationView, self).form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated():
            return simple.redirect_to(request, url=self.get_success_url(), permanent=False)
        return super(RegistrationView, self).dispatch(request, *args, **kwargs)

class AccountView(edit_views.FormView):
    """
    This view displays form for updating user settings. It checks if all fields are valid and updates user.
    """

    template_name = 'user/account.html'
    form_class = forms.AccountChangeForm
    success_url = urlresolvers.reverse_lazy('account')

    def form_valid(self, form):
        user = self.request.user
        user.first_name=form.cleaned_data['first_name']
        user.last_name=form.cleaned_data['last_name']
        user.email=form.cleaned_data['email']
        user.gender=form.cleaned_data['gender']
        user.birthdate=form.cleaned_data['birthdate']
        user.save()
        messages.success(self.request, _("You have successfully modified your account."))
        return super(AccountView, self).form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated():
            return shortcuts.redirect('login')
        return super(AccountView, self).dispatch(request, *args, **kwargs)

    def get_form(self, form_class):
        return form_class(self.request.user, **self.get_form_kwargs())

    def get_initial(self):
        return {
            'first_name': self.request.user.first_name,
            'last_name': self.request.user.last_name,
            'email': self.request.user.email,
            'gender': self.request.user.gender,
            'birthdate': self.request.user.birthdate,
        }

class PasswordChangeView(edit_views.FormView):
    """
    This view displays form for changing password.
    """

    template_name = 'user/password_change.html'
    form_class = forms.PasswordChangeForm
    success_url = urlresolvers.reverse_lazy('account')

    def form_valid(self, form):
        self.request.user.set_password(form.cleaned_data['password1'])
        messages.success(self.request, _("You have successfully changed your password."))
        return super(PasswordChangeView, self).form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated():
            return shortcuts.redirect('login')
        return super(PasswordChangeView, self).dispatch(request, *args, **kwargs)

    def get_form(self, form_class):
        return form_class(self.request.user, **self.get_form_kwargs())

@dispatch.receiver(signals.channel_subscribe)
def process_channel_subscribe(sender, request, channel_id, **kwargs):
    request.user.update(
        push__connections={
            'http_if_none_match': request.META['HTTP_IF_NONE_MATCH'],
            'http_if_modified_since': request.META['HTTP_IF_MODIFIED_SINCE'],
            'channel_id': channel_id,
        }
    )

@dispatch.receiver(signals.channel_unsubscribe)
def process_channel_unsubscribe(sender, request, channel_id, **kwargs):
    models.User.objects(
        id=request.user.id,
        connections__http_if_none_match=request.META['HTTP_IF_NONE_MATCH'],
        connections__http_if_modified_since=request.META['HTTP_IF_MODIFIED_SINCE'],
        connections__channel_id=channel_id,
    ).update_one(unset__connections__S=1)

    request.user.update(
        pull__connections=None,
        set__connection_last_unsubscribe=datetime.datetime.now(),
    )
