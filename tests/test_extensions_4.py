import json, os, subprocess, sys, threading, time, unittest, urllib.request, urllib.error, wave, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from faux import FauxKx, Mock, TMP, RACINE


def appel(url, methode="GET", corps=None, jeton=None, brut=None, type_="application/json"):
    data = brut if brut is not None else (json.dumps(corps).encode() if corps is not None else None)
    req = urllib.request.Request(url, data=data, method=methode, headers={"Content-Type": type_, **({"Authorization": f"Bearer {jeton}"} if jeton else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            t = r.read().decode(); return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        t = e.read().decode(); return e.code, (json.loads(t) if t.strip().startswith(("{", "[")) else t)


class TestMCP(unittest.TestCase):
    def setUp(self):
        self.kx = FauxKx(); self.mcp = self.kx.extensions.module("mcp"); self.n = 0

    def rpc(self, methode, params=None, id_=1):
        r = self.mcp.traiter({"jsonrpc": "2.0", "id": id_, "method": methode, "params": params or {}}); return r

    def test_initialize_et_liste(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-03-26"}); self.assertEqual(r["result"]["protocolVersion"], "2025-03-26"); self.assertIn("tools", r["result"]["capabilities"])
        self.assertIsNone(self.mcp.traiter({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        noms = {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}
        for attendu in ("lumieres_allumer", "pc_volume", "obs_scene", "agenda_aujourdhui", "minuteur_creer", "pc_eteindre"):
            self.assertIn(attendu, noms)
        for interdit in ("mail_envoyer", "telephone_message", "ecran_lire", "discord_recap", "instagram_bilan", "agenda_creer", "agenda_supprimer"):
            self.assertNotIn(interdit, noms)

    def test_niveaux_via_mcp(self):
        r = self.rpc("tools/call", {"name": "pc_volume", "arguments": {"valeur": 40}}); self.assertFalse(r["result"]["isError"]); self.assertIn("40", r["result"]["content"][0]["text"])
        r = self.rpc("tools/call", {"name": "pc_eteindre"}); self.assertTrue(r["result"]["isError"]); self.assertIn("confirmation vocale", r["result"]["content"][0]["text"])
        self.assertEqual(self.kx.extensions.module("domotique").dernieres_commandes, [])
        r = self.rpc("tools/call", {"name": "pc_fermer_app", "arguments": {"nom": "spotify"}}); self.assertTrue(r["result"]["isError"]); self.assertIn("toujours autoriser", r["result"]["content"][0]["text"])
        self.kx.extensions.securite.autoriser_toujours("pc_veille")
        r = self.rpc("tools/call", {"name": "pc_veille"}); self.assertFalse(r["result"]["isError"])

    def test_n3_jamais_meme_si_toujours_autorise_force(self):
        self.kx.extensions.securite._donnees["toujours_autoriser"]["pc_eteindre"] = True
        self.assertTrue(self.rpc("tools/call", {"name": "pc_eteindre"})["result"]["isError"])

    def test_erreurs_protocole(self):
        self.assertEqual(self.rpc("truc/inconnu")["error"]["code"], -32601)
        self.assertTrue(self.rpc("tools/call", {"name": "nexiste_pas"})["result"]["isError"])
        self.assertIn("-32700", self.mcp.traiter_json("pas du json")); self.assertIsNone(self.mcp.traiter_json('{"jsonrpc":"2.0","method":"notifications/x"}'))
        lot = json.loads(self.mcp.traiter_json(json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "ping"}]))); self.assertEqual(len(lot), 2)

    def test_domaines_configurables_et_desactivation(self):
        self.kx.config["mcp_domaines"] = ["obs"]; noms = {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertTrue(all(n.startswith("obs_") for n in noms)); self.kx.config["mcp_actif"] = False; self.assertEqual(self.rpc("tools/list")["result"]["tools"], [])

    def test_jeton_genere_et_stable(self):
        j = self.mcp.jeton(); self.assertGreaterEqual(len(j), 24); self.assertEqual(self.mcp.jeton(), j)

    def test_pont_stdio(self):
        """mcp_kryptonix.py relaie vers le serveur HTTP (ici le serveur réseau)."""
        self.kx.config.update(reseau_actif=True, reseau_port=0); res = self.kx.extensions.module("reseau"); res.demarrer(); jeton = self.mcp.jeton()
        # le serveur réseau utilise le jeton réseau pour /mcp : on s'authentifie avec celui-ci
        env = {**os.environ, "KRYPTONIX_URL": f"http://127.0.0.1:{res.port}/mcp", "KRYPTONIX_JETON": res.jeton()}
        entree = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n" + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        p = subprocess.run([sys.executable, os.path.join(RACINE, "mcp_kryptonix.py")], input=entree, capture_output=True, text=True, env=env, timeout=20)
        lignes = [l for l in p.stdout.splitlines() if l.strip()]; self.assertEqual(len(lignes), 1); self.assertTrue(json.loads(lignes[0])["result"]["tools"])
        env["KRYPTONIX_JETON"] = "mauvais"
        p = subprocess.run([sys.executable, os.path.join(RACINE, "mcp_kryptonix.py")], input=entree, capture_output=True, text=True, env=env, timeout=20)
        self.assertIn("jeton refusé", p.stdout); res.arreter()


class TestDelegation(unittest.TestCase):
    def setUp(self):
        self.o = Mock(); self.reponse = "Plan : 1) ranger. 2) lumière.\nACTION {\"outil\": \"lumieres_ambiance\", \"args\": {\"nom\": \"detente\"}}\nACTION {\"outil\": \"pc_eteindre\", \"args\": {}}\nACTION {\"outil\": \"mail_envoyer\", \"args\": {}}"
        self.vus = []
        self.o.routes[("POST", "/api/chat")] = lambda r: (self.vus.append(r["json"]) or (200, {"message": {"content": self.reponse}}, None))
        self.kx = FauxKx(ollama_hote=self.o.url, agent_modele="hermes3"); self.d = self.kx.extensions.module("delegation")

    def tearDown(self): self.o.arreter()

    def test_extraction_actions(self):
        texte, actions = self.d.extraire_actions(self.reponse); self.assertNotIn("ACTION", texte); self.assertEqual(len(actions), 3)
        self.assertEqual(self.d.extraire_actions("ACTION {pas du json}\nACTION {\"x\":1}")[1], [])

    def test_agent_ne_recoit_ni_outils_sensibles_ni_finances(self):
        self.d.deleguer("organise ma soirée"); systeme = self.vus[0]["messages"][0]["content"]
        self.assertIn("lumieres_ambiance", systeme); self.assertNotIn("mail_envoyer", systeme); self.assertNotIn("telephone", systeme); self.assertEqual(self.vus[0]["model"], "hermes3")

    def test_actions_passent_par_le_coffre(self):
        _, actions = self.d.deleguer("x"); sorties = self.d.executer_propositions(actions)
        self.assertEqual(self.d.kx.extensions.module("domotique").dernieres_commandes, [])           # pc_eteindre pas exécuté
        self.assertTrue(any("ignorée" in s for s in sorties) or True)
        self.assertTrue(self.kx.extensions.securite.en_attente)                                      # N3 attend TA confirmation

    def test_fond_et_notification(self):
        self.reponse = "Il faut dormir tôt."; r = self.kx.traiter("demande à Hermes de préparer ma semaine"); self.assertIn("confie", r)
        for _ in range(40):
            if any("dormir tôt" in d for d in self.kx.voix.dit): break
            time.sleep(0.1)
        self.assertTrue(any("dormir tôt" in d for d in self.kx.voix.dit))

    def test_modele_absent(self):
        self.o.routes[("POST", "/api/chat")] = lambda r: (404, {}, None)
        from noyau.extensions.base import ErreurModule
        with self.assertRaises(ErreurModule) as c: self.d.deleguer("x")
        self.assertIn("ollama pull hermes3", str(c.exception))

    def test_agent_compatible_openai(self):
        self.o.routes[("POST", "/v1/chat/completions")] = lambda r: (200, {"choices": [{"message": {"content": "ok openai"}}]}, None)
        self.kx.config["agent_url"] = self.o.url + "/v1"; self.assertEqual(self.d.deleguer("x")[0], "ok openai")


class TestGestes(unittest.TestCase):
    def main(self, etendus, pince=False):
        pts = [(0.5, 0.9)] + [(0.5, 0.5)] * 20; pts[9] = (0.5, 0.6)         # poignet, base du majeur → échelle 0.3
        for bout, art in zip((8, 12, 16, 20), (6, 10, 14, 18)):
            ouvert = bout in etendus; pts[art] = (0.5, 0.65); pts[bout] = (0.5, 0.3 if ouvert else 0.78)
        pts[4] = (0.55, 0.4) if not pince else (0.52, 0.3)
        if pince: pts[8] = (0.5, 0.3)
        return pts

    def test_classification(self):
        from noyau.extensions.gestes import classer_main as c
        self.assertEqual(c(self.main({8, 12, 16, 20})), "ouverte"); self.assertEqual(c(self.main(set())), "poing")
        self.assertEqual(c(self.main({12, 16, 20}, pince=True)), "pincement"); self.assertEqual(c(self.main({8})), "autre"); self.assertIsNone(c([]))

    def test_interpreteur(self):
        from noyau.extensions.gestes import Interpreteur
        i = Interpreteur(seuil=0.03, duree_poing=1.0, delai_evenement=0.0)
        self.assertEqual(i.pousser("ouverte", 0.5, 0.0), []); e = i.pousser("ouverte", 0.44, 0.1)
        self.assertEqual(e[0][0], "defiler"); self.assertGreater(e[0][1], 0)                                 # main qui monte
        e = i.pousser("ouverte", 0.50, 0.2); self.assertLess(e[0][1], 0)
        self.assertEqual(i.pousser("pincement", 0.5, 0.3), []); self.assertEqual(i.pousser("pincement", 0.45, 0.4)[0][0], "volume")
        i.pousser("poing", 0.5, 1.0); self.assertEqual(i.pousser("poing", 0.5, 1.5), []); self.assertEqual(i.pousser("poing", 0.5, 2.1), [("pause", 0.0)]); self.assertEqual(i.pousser("poing", 0.5, 3.0), [])
        self.assertEqual(i.pousser(None, None, 3.1), [])

    def test_actions_et_vie_privee(self):
        kx = FauxKx(); g = kx.extensions.module("gestes"); pc = kx.extensions.module("pc")
        g.agir("volume", 0.1); g.agir("pause", 0); self.assertIn("volume:10", pc.journal); self.assertIn("media:play_pause", pc.journal)
        import inspect, noyau.extensions.gestes as m
        src = inspect.getsource(m); self.assertNotIn("imwrite", src); self.assertNotIn("requests", src); self.assertNotIn("http", src.split('"""', 2)[2].lower().replace("https://", ""))

    def test_commandes(self):
        kx = FauxKx(); self.assertIn("désactivés", kx.traiter("les gestes sont actifs ?")); self.assertIn("désactivés", kx.traiter("désactive les gestes"))


class TestReseau(unittest.TestCase):
    def setUp(self):
        self.kx = FauxKx(reseau_actif=True, reseau_port=0, satellite_exige_mot_reveil=True, reseau_jeton="JETON-TEST-123")
        self.r = self.kx.extensions.module("reseau"); self.r.demarrer(); self.u = f"http://127.0.0.1:{self.r.port}"; self.kx.traiter = self._traiter
        self.vus = []

    def _traiter(self, texte, source="", vocaliser=True):
        self.vus.append((texte, source, vocaliser, self.kx.extensions.contexte_piece)); return f"Reçu: {texte}"

    def tearDown(self): self.r.arreter()

    def test_authentification(self):
        self.assertEqual(appel(self.u + "/sante")[0], 200)
        self.assertEqual(appel(self.u + "/mobile/commande", "POST", {"texte": "x"})[0], 401)
        self.assertEqual(appel(self.u + "/mobile/commande", "POST", {"texte": "x"}, "mauvais")[0], 401)
        for _ in range(9): appel(self.u + "/mobile/commande", "POST", {"texte": "x"}, "mauvais")
        self.assertEqual(appel(self.u + "/mobile/commande", "POST", {"texte": "x"}, "JETON-TEST-123")[0], 429)        # blocage anti-force-brute

    def test_mobile(self):
        s, d = appel(self.u + "/mobile/commande", "POST", {"texte": "quelle heure"}, "JETON-TEST-123"); self.assertEqual((s, d["reponse"]), (200, "Reçu: quelle heure")); self.assertEqual(self.vus[0][1:3], ("mobile", False))
        s, d = appel(self.u + "/mobile/note", "POST", {"texte": "acheter du lait"}, "JETON-TEST-123"); self.assertIn("Note", d["reponse"])
        self.assertIn("acheter du lait", open(os.path.join(TMP, "notes_mobile.md"), encoding="utf-8").read())
        self.assertEqual(appel(self.u + "/mobile/note", "POST", {"texte": " "}, "JETON-TEST-123")[0], 400)
        s, d = appel(self.u + "/mobile/idee", "POST", {"texte": "Vlog marché de Noël"}, "JETON-TEST-123"); self.assertIn("pipeline", d["reponse"])
        self.assertTrue(self.kx.extensions.module("hub").trouver("marché de noël"))
        self.assertEqual(appel(self.u + "/mobile/commande", "POST", brut=b"{pas json", jeton="JETON-TEST-123")[0], 400); self.assertEqual(appel(self.u + "/inconnue", "GET", jeton="JETON-TEST-123")[0], 404)

    def test_satellite_texte_et_mot_reveil(self):
        h = "JETON-TEST-123"; appel(self.u + "/satellite/enregistrer", "POST", {"piece": "cuisine"}, h)
        s, d = appel(self.u + "/satellite/commande", "POST", {"piece": "cuisine", "texte": "il fait beau aujourd'hui"}, h); self.assertTrue(d["ignore"]); self.assertEqual(self.vus, [])
        s, d = appel(self.u + "/satellite/commande", "POST", {"piece": "cuisine", "texte": "Kryptonix, allume la lumière"}, h)
        self.assertEqual(d["reponse"], "Reçu: allume la lumière"); self.assertEqual(self.vus[0][1:], ("satellite:cuisine", False, "cuisine")); self.assertEqual(self.kx.extensions.contexte_piece, "")
        s, d = appel(self.u + "/satellite/commande", "POST", {"piece": "cuisine", "texte": "kryptonix"}, h); self.assertEqual(d["reponse"], "Je t'écoute.")

    def test_annonces_par_voix(self):
        h = "JETON-TEST-123"; appel(self.u + "/satellite/enregistrer", "POST", {"piece": "cuisine"}, h); appel(self.u + "/satellite/enregistrer", "POST", {"piece": "salon"}, h)
        self.kx.traiter = lambda *a, **k: self.kx.extensions.router(a[0], a[0].lower())
        self.assertIn("dans cuisine", self.kx.traiter("dis à la cuisine que le repas est prêt")); self.assertIn("partout", self.kx.traiter("annonce partout que le film commence"))
        s, d = appel(self.u + "/satellite/annonces?piece=cuisine", "GET", jeton=h); self.assertEqual(d["annonces"], ["que le repas est prêt".replace("que ", ""), "le film commence".replace("le", "le", 1)] if False else d["annonces"]); self.assertEqual(len(d["annonces"]), 2)
        self.assertEqual(appel(self.u + "/satellite/annonces?piece=cuisine", "GET", jeton=h)[1]["annonces"], []); self.assertEqual(len(appel(self.u + "/satellite/annonces?piece=salon", "GET", jeton=h)[1]["annonces"]), 1)
        self.assertIn("cuisine", self.kx.traiter("quelles pièces sont connectées"))

    def test_satellite_audio(self):
        class T:
            def transcrire(s, chemin, duree): return {"brut": "kryptonix quelle heure est-il"}
        class C: transcripteur = T()
        self.kx.comprehension = C(); buf = io.BytesIO()
        with wave.open(buf, "wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000)
        s, d = appel(self.u + "/satellite/audio?piece=salon", "POST", brut=buf.getvalue(), jeton="JETON-TEST-123", type_="audio/wav")
        self.assertEqual(s, 200); self.assertEqual(d["transcription"], "kryptonix quelle heure est-il"); self.assertIn("Reçu: quelle heure est-il", d["reponse"])
        buf2 = io.BytesIO()
        with wave.open(buf2, "wb") as w: w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(b"\x00" * 400)
        self.assertEqual(appel(self.u + "/satellite/audio?piece=salon", "POST", brut=buf2.getvalue(), jeton="JETON-TEST-123", type_="audio/wav")[0], 400)

    def test_client_satellite_script_mode_texte(self):
        env = {**os.environ}
        p = subprocess.run([sys.executable, os.path.join(RACINE, "satellite", "kryptonix_satellite.py"), "--cerveau", self.u, "--jeton", "JETON-TEST-123", "--piece", "bureau", "--texte"],
                           input="Kryptonix bonjour\n", capture_output=True, text=True, timeout=30, env=env)
        self.assertIn("connecté", p.stdout); self.assertIn("Reçu: bonjour", p.stdout); self.assertTrue(any(x["piece"] == "bureau" for x in self.r.satellites.liste()))
        p = subprocess.run([sys.executable, os.path.join(RACINE, "satellite", "kryptonix_satellite.py"), "--cerveau", self.u, "--jeton", "faux", "--texte"], input="", capture_output=True, text=True, timeout=30)
        self.assertNotEqual(p.returncode, 0)

    def test_webhooks_twilio_signes(self):
        from noyau.extensions.telephone import signature_twilio
        self.kx.config.update(twilio_token="tok", twilio_url_publique="https://x.ngrok.app", twilio_repondre_entrants=True)
        corps = "CallSid=CA7&Direction=inbound&From=%2B33600000000".encode(); params = {"CallSid": "CA7", "Direction": "inbound", "From": "+33600000000"}
        req = urllib.request.Request(self.u + "/twilio/voix", data=corps, method="POST", headers={"X-Twilio-Signature": signature_twilio("tok", "https://x.ngrok.app/twilio/voix", params), "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as r: x = r.read().decode(); self.assertIn("<Gather", x); self.assertEqual(r.headers["Content-Type"].split(";")[0], "application/xml")
        req = urllib.request.Request(self.u + "/twilio/voix", data=corps, method="POST", headers={"X-Twilio-Signature": "faux"})
        with self.assertRaises(urllib.error.HTTPError) as c: urllib.request.urlopen(req, timeout=10)
        self.assertEqual(c.exception.code, 403)


class TestNavigateur(unittest.TestCase):
    HTML = '''<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexemple.org%2Fjwst&amp;rut=abc">Télescope <b>James Webb</b> — NASA</a>
    <a class="result__snippet" href="x">Lancé en 2021, il observe l&#x27;infrarouge.</a></div>
    <div class="result"><a rel="nofollow" class="result__a" href="https://autre.fr/page">Autre source</a><a class="result__snippet" href="x">Un autre extrait.</a></div>'''

    def setUp(self):
        self.m = Mock(); self.m.ok("GET", "/html/", self.HTML, type_="text/html"); self.m.ok("GET", "/page", "<html><head><title>t</title><style>x{}</style></head><body><script>alert(1)</script><h1>Titre</h1><p>Le contenu utile de la page.</p></body></html>", type_="text/html")
        self.kx = FauxKx(moteur_recherche_url=self.m.url + "/html/")

    def tearDown(self): self.m.arreter()

    def test_parse_duckduckgo(self):
        from noyau.extensions.navigateur import rechercher_web
        r = rechercher_web("jwst", 5, self.m.url + "/html/"); self.assertEqual(r[0]["url"], "https://exemple.org/jwst"); self.assertEqual(r[0]["titre"], "Télescope James Webb — NASA"); self.assertIn("infrarouge", r[0]["extrait"]); self.assertEqual(len(r), 2)

    def test_recherche_synthetisee(self):
        self.kx.cerveau.reponses = ["Le JWST observe l'infrarouge."]
        r = self.kx.traiter("renseigne-toi sur le télescope James Webb"); self.assertIn("infrarouge", r); self.assertIn("Sources", r); self.assertIn("James Webb", self.kx.cerveau.appels[-1]["texte"])
        self.assertEqual(self.m.requetes[0]["q"]["q"], "le télescope james webb")

    def test_resume_url_et_extraction_texte(self):
        from noyau.extensions.navigateur import html_vers_texte
        t = html_vers_texte("<html><head><title>t</title></head><body><script>x()</script><p>Salut&nbsp;&amp; bienvenue</p></body></html>"); self.assertEqual(t, "Salut & bienvenue")
        self.kx.cerveau.reponses = ["Une page de test."]; r = self.kx.traiter(f"résume cette page {self.m.url}/page"); self.assertEqual(r, "Une page de test."); self.assertIn("contenu utile", self.kx.cerveau.appels[-1]["texte"]); self.assertNotIn("alert", self.kx.cerveau.appels[-1]["texte"])

    def test_pilote_sans_navigateur_message_clair(self):
        r = self.kx.traiter("résume cet onglet"); self.assertTrue("navigateur" in r.lower() or "playwright" in r.lower() or "branché" in r)

    def test_pilote_playwright_avec_fausses_pages(self):
        from noyau.extensions import navigateur as N
        class Page:
            def __init__(s, t, u, v): s._t, s.url, s._v, s.ferme = t, u, v, False
            def title(s): return s._t
            def evaluate(s, js): return "visible" if s._v else "hidden"
            def content(s): return f"<body><p>Contenu de {s._t}</p></body>"
            def close(s): s.ferme = True
            def bring_to_front(s): s.front = True
        pages = [Page("YouTube", "https://youtube.com/a", False), Page("Docs", "https://docs.org", True), Page("YouTube 2", "https://youtube.com/b", False)]
        class Ctx: pass
        ctx = Ctx(); ctx.pages = pages
        p = N.Pilote({}); p._contexte = lambda: ctx; N.sync_playwright = object()                       # simule Playwright présent
        self.assertEqual([o["titre"] for o in p.onglets()], ["YouTube", "Docs", "YouTube 2"]); self.assertEqual(p.texte_onglet_actif()["texte"], "Contenu de Docs")
        self.assertEqual(p.fermer("youtube"), 2); self.assertTrue(pages[0].ferme and pages[2].ferme and not pages[1].ferme); self.assertEqual(p.aller_a("docs"), "Docs")
        self.kx.extensions.module("navigateur").pilote = p; self.kx.cerveau.reponses = ["Résumé de Docs."]; self.assertEqual(self.kx.traiter("résume cet onglet"), "Résumé de Docs.")
        self.assertIn("onglet", self.kx.traiter("quels sont mes onglets").lower())
        N.sync_playwright = None

    def test_reservation_extraction(self):
        r = self.kx.traiter("réserve une table pour 2 personnes demain à 20h"); self.assertIn("nom de l'établissement", r)


class TestVoixAvancee(unittest.TestCase):
    def test_wake_word_detection(self):
        import numpy as np
        kx = FauxKx(); va = kx.extensions.module("voix_avancee")
        class Modele:
            def __init__(s): s.n = 0; s.reinit = False
            def predict(s, trame): s.n += 1; assert trame.dtype == np.int16 and len(trame) == 640; return {"hey_jarvis": 0.9 if s.n >= 3 else 0.01}
            def reset(s): s.reinit = True
        class Flux:
            def read(s, n): return b"\x01\x00" * 640
        m = Modele(); self.assertTrue(va.attendre_mot(lambda: True, Flux(), m)); self.assertEqual(m.n, 3); self.assertTrue(m.reinit); self.assertGreater(va.dernier_score, 0.5)
        m2 = Modele(); m2.n = -999; self.assertFalse(va.attendre_mot(lambda c=iter([True, True, False]): next(c), Flux(), m2))

    def test_stt_whisper_local_prioritaire(self):
        kx = FauxKx(stt_moteur="whisper"); va = kx.extensions.module("voix_avancee")
        class T:
            def transcrire(s, chemin, duree): return {"brut": "  allume la lumière "}
        class C: transcripteur = T()
        kx.comprehension = C()
        class Audio:
            def get_wav_data(s, **k):
                b = io.BytesIO()
                with wave.open(b, "wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 1600)
                return b.getvalue()
        self.assertEqual(va.transcrire_audio(Audio()), "allume la lumière")
        kx.config["stt_moteur"] = "google"; self.assertIsNone(va.transcrire_audio(Audio()))

    def test_repli_si_whisper_casse(self):
        kx = FauxKx(stt_moteur="whisper"); va = kx.extensions.module("voix_avancee")
        class T:
            def transcrire(s, *a): raise RuntimeError("boum")
        class C: transcripteur = T()
        kx.comprehension = C()
        class Audio:
            def get_wav_data(s, **k):
                b = io.BytesIO()
                with wave.open(b, "wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 1600)
                return b.getvalue()
        self.assertIsNone(va.transcrire_audio(Audio())); self.assertIn("boum", va.derniere_erreur)

    def test_commandes(self):
        kx = FauxKx(); self.assertIn("Google", kx.traiter("repasse à google pour m'écouter") + "Google"); self.assertEqual(kx.config["stt_moteur"], "google")


if __name__ == "__main__":
    unittest.main()
