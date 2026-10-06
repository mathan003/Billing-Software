"""
Unified Windows Application Architecture:
The Windows Desktop App uses the exact same modern, responsive, bilingual Web UI/UX
as the website, served through an embedded local Waitress WSGI server and Edge WebView2
via windows_app.app.main().
"""
from windows_app.app import main
