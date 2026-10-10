#!/usr/bin/env python3
"""
Autorisation Google Agenda (une seule fois) — obtient le jeton de rafraîchissement.

1. console.cloud.google.com → nouveau projet → « API Google Calendar » : Activer.
2. « Identifiants » → Créer → ID client OAuth → type « Application de bureau ». Note l'ID et le secret.
3. Écran de consentement : ajoute ton adresse comme utilisateur test.
4. Lance :  python scripts/google_autorisation.py  <ID_CLIENT>  <SECRET>
   Ton navigateur s'ouvre ; autorise l'accès. Le script écrit google_client_id, google_client_secret
   et google_refresh_token dans config.json.
"""
import http.server
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

PORTEE = "https://www.googleapis.com/auth/calendar"
CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    client_id, secret = sys.argv[1], sys.argv[2]
    code: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code["v"] = (q.get("code") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Autorisation reçue. Tu peux fermer cet onglet.</h2>".encode())

        def log_message(self, *a):
            pass

    serveur = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{serveur.server_address[1]}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": PORTEE,
        "access_type": "offline", "prompt": "consent"})
    threading.Thread(target=serveur.handle_request, daemon=True).start()
    print("Ouverture du navigateur…")
    webbrowser.open(url)
    for _ in range(300):
        if code.get("v"):
            break
        threading.Event().wait(1)
    if not code.get("v"):
        print("Aucune autorisation reçue.")
        return 1
    donnees = urllib.parse.urlencode({"code": code["v"], "client_id": client_id, "client_secret": secret,
                                      "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=donnees)) as r:
        jetons = json.loads(r.read())
    if "refresh_token" not in jetons:
        print("Pas de jeton de rafraîchissement : révoque l'accès dans ton compte Google puis recommence.")
        return 1
    config = {}
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            config = json.load(f)
    config.update(google_client_id=client_id, google_client_secret=secret, google_refresh_token=jetons["refresh_token"])
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    print("Google Agenda est autorisé. Redémarre Kryptonix.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
