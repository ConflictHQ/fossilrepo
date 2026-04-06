import threading

_thread_local = threading.local()


def get_current_user():
    return getattr(_thread_local, "user", None)


class CurrentUserMiddleware:
    """Store the current user on thread-local storage for use in signals and model save methods."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_local.user = getattr(request, "user", None)
        response = self.get_response(request)
        return response
