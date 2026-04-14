"""
Jupyter Server configuration for fossilrepo spoke.

Configures jupyter-server-proxy to route /fossilrepo → gunicorn on port 8000.
absolute_url=False strips the /fossilrepo prefix so Django sees clean paths
(e.g. /dashboard/ instead of /user/.../fossilrepo/dashboard/).
"""
import os

from traitlets.config import get_config

c = get_config()

c.ServerApp.allow_origin = "*"
c.ServerApp.allow_credentials = True
c.ServerApp.disable_check_xsrf = True
c.ServerApp.trust_xheaders = True
c.ServerApp.default_url = "/fossilrepo"

c.ServerApp.jpserver_extensions = {
    "jupyter_server_proxy": True,
}

c.ServerProxy.servers = {
    "fossilrepo": {
        "command": ["echo", "Fossilrepo gunicorn is already running on port 8000"],
        "port": 8000,
        "timeout": 120,
        "absolute_url": False,
        "launcher_entry": {"enabled": False},
        "new_browser_tab": False,
    }
}

c.ServerProxy.host_allowlist = ["localhost", "127.0.0.1", "0.0.0.0"]

# Fix OAuth callback URL for named servers
if os.environ.get("JUPYTERHUB_SERVER_NAME"):
    server_name = os.environ["JUPYTERHUB_SERVER_NAME"]
    username = os.environ.get("JUPYTERHUB_USER", "")
    correct_callback = f"/user/{username}/{server_name}/oauth_callback"
    if os.environ.get("JUPYTERHUB_OAUTH_CALLBACK_URL") != correct_callback:
        os.environ["JUPYTERHUB_OAUTH_CALLBACK_URL"] = correct_callback

print("jupyter_server_config.py loaded: fossilrepo → port 8000 (prefix-stripped)")
