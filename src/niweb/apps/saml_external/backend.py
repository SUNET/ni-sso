from django.contrib.auth.backends import RemoteUserBackend

class XRemoteUserBackend(RemoteUserBackend):
    create_unknown_user = True

    def configure_user(self, request, user):
        user.is_superuser = True
        user.is_staff = True
        user.save()
        return user
