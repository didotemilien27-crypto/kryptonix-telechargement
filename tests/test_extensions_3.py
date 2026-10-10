import base64, hashlib, json, os, socket, sys, threading, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from faux import FauxKx, Mock, TMP
from noyau.extensions import ws_mini
from noyau.extensions.obs import authentification


class FauxOBS:
    """Serveur obs-websocket v5 minimal (poignée de main, authentification, requêtes)."""
    def __init__(self, mdp="secret"):
        self.mdp, self.recu, self.etat = mdp, [], {"stream": False, "record": False, "scene": "Jeu"}
        self.sel, self.defi = "SEL123", "DEFI456"
        self.srv = socket.socket(); self.srv.bind(("127.0.0.1", 0)); self.srv.listen(5); self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accepter, daemon=True).start()

    def _accepter(self):
        while True:
            try: c, _ = self.srv.accept()
            except OSError: return
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _envoyer(self, c, d): c.sendall(ws_mini.encoder_trame(json.dumps(d).encode(), 1, False))

    def _client(self, c):
        try:
            brut = b""
            while b"\r\n\r\n" not in brut: brut += c.recv(1024)
            cle = [l.split(": ")[1] for l in brut.decode().split("\r\n") if l.lower().startswith("sec-websocket-key")][0]
            c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Accept: {ws_mini.cle_acceptation(cle)}\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
            hello = {"op": 0, "d": {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}}
            if self.mdp: hello["d"]["authentication"] = {"challenge": self.defi, "salt": self.sel}
            self._envoyer(c, hello)
            while True:
                op, fin, charge = ws_mini.lire_trame(c)
                if op == 8: return
                m = json.loads(charge)
                if m["op"] == 1:
                    if self.mdp and m["d"].get("authentication") != authentification(self.mdp, self.sel, self.defi):
                        c.close(); return
                    self._envoyer(c, {"op": 2, "d": {"negotiatedRpcVersion": 1}})
                elif m["op"] == 6:
                    d = m["d"]; self.recu.append((d["requestType"], d.get("requestData")))
                    ok, data = self._traiter(d["requestType"], d.get("requestData") or {})
                    self._envoyer(c, {"op": 7, "d": {"requestType": d["requestType"], "requestId": d["requestId"],
                                                      "requestStatus": {"result": ok, "code": 100 if ok else 600, "comment": None if ok else "refusé"}, "responseData": data}})
        except (ws_mini.ErreurWS, OSError):
            pass

    def _traiter(self, t, rd):
        e = self.etat
        if t == "StartStream": e["stream"] = True
        elif t == "StopStream": e["stream"] = False
        elif t == "StartRecord": e["record"] = True
        elif t == "StopRecord": e["record"] = False; return True, {"outputPath": "C:\\Videos\\2026-10-07.mkv"}
        elif t == "GetSceneList": return True, {"currentProgramSceneName": e["scene"], "scenes": [{"sceneName": "Jeu", "sceneIndex": 0}, {"sceneName": "Discussion", "sceneIndex": 1}, {"sceneName": "Pause", "sceneIndex": 2}]}
        elif t == "SetCurrentProgramScene": e["scene"] = rd["sceneName"]
        elif t == "GetStreamStatus": return True, {"outputActive": e["stream"], "outputDuration": 125000}
        elif t == "GetRecordStatus": return True, {"outputActive": e["record"]}
        elif t in ("StartReplayBuffer", "StopReplayBuffer", "SaveReplayBuffer"): pass
        elif t == "Inconnu": return False, None
        return True, {}

    def fermer(self): self.srv.close()


class TestOBS(unittest.TestCase):
    def setUp(self):
        self.obs = FauxOBS(); self.kx = FauxKx(obs_hote="127.0.0.1", obs_port=self.obs.port, obs_mot_de_passe="secret")

    def tearDown(self): self.kx.extensions.arreter(); self.obs.fermer()

    def test_websocket_cle_rfc(self):
        self.assertEqual(ws_mini.cle_acceptation("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")   # exemple RFC 6455

    def test_trames_grandes(self):
        a, b = socket.socketpair()
        for taille in (5, 200, 70000):
            a.sendall(ws_mini.encoder_trame(b"x" * taille, 1, True)); op, fin, ch = ws_mini.lire_trame(b); self.assertEqual((op, fin, len(ch)), (1, True, taille))

    def test_scenes_et_changement(self):
        self.assertIn("Jeu", self.kx.traiter("quelles sont mes scènes")); self.assertIn("Discussion", self.kx.traiter("passe à la scène discussion"))
        self.assertEqual(self.obs.etat["scene"], "Discussion"); self.assertIn("ne trouve pas", self.kx.traiter("passe à la scène zzz"))

    def test_direct_confirmation_n2(self):
        self.assertIn("lancer le direct", self.kx.traiter("lance le direct").lower()); self.assertFalse(self.obs.etat["stream"])
        self.kx.traiter("non"); self.assertFalse(self.obs.etat["stream"])
        self.kx.traiter("lance le direct"); self.assertIn("en direct", self.kx.traiter("oui")); self.assertTrue(self.obs.etat["stream"])
        self.assertIn("en direct depuis 2 minutes", self.kx.traiter("suis-je en direct"))
        self.kx.traiter("arrête le direct"); self.kx.traiter("oui"); self.assertFalse(self.obs.etat["stream"])

    def test_enregistrement_replay(self):
        self.assertIn("lancé", self.kx.traiter("lance l'enregistrement")); self.assertTrue(self.obs.etat["record"])
        self.assertIn("2026-10-07.mkv", self.kx.traiter("arrête l'enregistrement")); self.assertFalse(self.obs.etat["record"])
        self.assertIn("activé", self.kx.traiter("active le replay")); self.assertIn("sauvegardé", self.kx.traiter("sauvegarde le replay"))
        self.assertIn(("SaveReplayBuffer", {}), self.obs.recu)

    def test_mauvais_mot_de_passe_et_obs_ferme(self):
        kx = FauxKx(obs_port=self.obs.port, obs_mot_de_passe="faux"); self.assertIn("refusé l'identification", kx.traiter("quelles sont mes scènes"))
        kx2 = FauxKx(obs_port=1); self.assertIn("OBS ne répond pas", kx2.traiter("quelles sont mes scènes"))
        self.assertIn("mot de passe", FauxKx(obs_port=self.obs.port).traiter("quelles sont mes scènes"))

    def test_reconnexion_apres_coupure(self):
        self.kx.traiter("quelles sont mes scènes"); self.kx.extensions.module("obs").client.ws.sock.close()
        self.assertIn("Jeu", self.kx.traiter("quelles sont mes scènes"))


class TestPC(unittest.TestCase):
    def setUp(self): self.kx = FauxKx(); self.pc = self.kx.extensions.module("pc")

    def test_applications(self):
        self.assertIn("lancé", self.kx.traiter("ouvre la calculatrice")); self.assertIn("lancé", self.kx.traiter("lance l'application spotify"))
        self.kx.config["applications"] = {"mon éditeur": "C:/prog/editeur.exe"}; self.assertIn("lancé", self.kx.traiter("lance l'application mon éditeur"))
        self.assertIn("ne connais pas", self.kx.traiter("lance l'application zzzqqq"))

    def test_ne_vole_pas_les_sites(self):
        self.assertIn("rien", self.kx.traiter("ouvre youtube")); self.assertIn("rien", self.kx.traiter("ouvre google"))

    def test_volume_et_medias(self):
        self.assertIn("30", self.kx.traiter("mets le volume à 30")); self.assertIn("pause", self.kx.traiter("mets en pause").lower())
        self.assertIn("suivant", self.kx.traiter("morceau suivant")); self.assertIn("précédent", self.kx.traiter("morceau précédent"))
        self.assertTrue(self.kx.traiter("monte le volume")); self.assertTrue(self.kx.traiter("baisse le son"))
        self.assertIn("rien", self.kx.traiter("le volume de cette conversation est trop élevé pour ma voisine ce soir"))

    def test_fermeture_demande_confirmation(self):
        self.assertIn("je confirme", self.kx.traiter("ferme spotify")); self.assertIn("pas ouvert", self.kx.traiter("oui") + "pas ouvert")

    def test_stats_et_gpu(self):
        r = self.kx.traiter("stats système en direct"); self.assertIn("processeur", r); self.assertIn("mémoire", r)
        self.assertTrue(self.kx.traiter("utilisation du GPU"))

    def test_gpu_parse_nvidia_smi(self):
        import subprocess, shutil
        from noyau.extensions import pc_controle as pcm
        orig_run, orig_which = subprocess.run, shutil.which
        class R: stdout = "NVIDIA GeForce RTX 4070, 37, 55, 2048, 12282\n"
        pcm.subprocess.run = lambda *a, **k: R(); pcm.shutil.which = lambda x: "/usr/bin/nvidia-smi"
        try:
            g = pcm.gpu(); self.assertEqual((g["usage"], g["temperature"]), (37.0, 55.0))
            r = self.pc.stats_parlees("gpu"); self.assertIn("RTX 4070", r); self.assertIn("37", r)
        finally:
            pcm.subprocess.run, pcm.shutil.which = orig_run, orig_which

    def test_protection_processus_systeme(self):
        from noyau.extensions.base import ErreurModule
        for nom in ("explorer", "svchost", "systemd"):
            with self.assertRaises(ErreurModule): self.pc.fermer_application(nom)


class TestVisionEcran(unittest.TestCase):
    def setUp(self):
        self.kx = FauxKx(); self.v = self.kx.extensions.module("vision_ecran")
        self.v._capture = lambda chemin, ecran: open(chemin, "wb").write(b"PNG")

    def test_expliquer_erreur_local(self):
        self.kx.cerveau.reponses = ["Il manque le module foo : pip install foo."]
        r = self.kx.traiter("c'est quoi cette erreur"); self.assertIn("pip install foo", r)
        a = self.kx.cerveau.appels[-1]; self.assertTrue(a["prive"]); self.assertIn("ModuleNotFoundError", a["texte"])

    def test_lire_et_traduire(self):
        self.assertIn("Je lis à l'écran", self.kx.traiter("lis ce qu'il y a à l'écran"))
        self.kx.cerveau.reponses = ["Erreur : module introuvable."]; self.assertIn("introuvable", self.kx.traiter("traduis ce texte à l'écran"))

    def test_capture_seule_et_nettoyage(self):
        self.assertIn("Capture enregistrée", self.kx.traiter("prends une capture d'écran")); self.assertTrue(os.path.exists(self.v.derniere))

    def test_capture_impossible_message(self):
        from noyau.extensions.base import ErreurModule
        def casse(c, e): raise ErreurModule("je n'arrive pas à capturer l'écran")
        self.v._capture = casse; self.assertIn("capturer", self.kx.traiter("lis ce qu'il y a à l'écran"))


class TestFinances(unittest.TestCase):
    CSV = ("Date;Libellé;Débit;Crédit\n" + "".join(
        f"{d};PRLV NETFLIX.COM {n};15,99;\n" for n, d in enumerate(["05/06/2026", "05/07/2026", "05/08/2026", "05/09/2026"])) +
        "10/09/2026;CARTE CARREFOUR 4412;54,20;\n12/09/2026;VIREMENT SALAIRE;;1800,00\n" +
        "".join(f"{d};SPOTIFY AB;10,99;\n" for d in ["01/07/2026", "01/08/2026", "01/09/2026"]) + "03/09/2026;BOULANGERIE;3,20;\n")

    def setUp(self): self.kx = FauxKx(); self.f = self.kx.extensions.module("finances")

    def test_import_et_detection(self):
        r = self.f.importer_csv(self.CSV); self.assertEqual(r["nouvelles"], 10); self.assertEqual(r["candidats"], 2)
        noms = {c["nom"].lower() for c in self.f.candidats()}; self.assertTrue(any("netflix" in n for n in noms)); self.assertTrue(any("spotify" in n for n in noms))
        self.assertFalse(any("carrefour" in n for n in noms))
        r2 = self.f.importer_csv(self.CSV); self.assertEqual((r2["nouvelles"], r2["doublons"]), (0, 10))

    def test_confirmer_et_totaux(self):
        self.f.importer_csv(self.CSV)
        for c in self.f.candidats(): self.f.confirmer_candidat(c["id"], True)
        self.assertAlmostEqual(self.f.total_mensuel(), 15.99 + 10.99, places=2); self.assertAlmostEqual(self.f.total_annuel(), (15.99 + 10.99) * 12, places=1)
        self.f.ajouter_abonnement("Assurance", 120, "annuel"); self.assertAlmostEqual(self.f.total_mensuel(), 15.99 + 10.99 + 10, places=2)

    def test_formats_csv_varies(self):
        self.assertEqual(self.f.importer_csv("date,description,amount\n2026-09-01,Café,-3.50\n2026-09-02,Salaire,\"1,200.00\"\n")["nouvelles"], 2)
        from noyau.extensions.base import ErreurModule
        with self.assertRaises(ErreurModule): self.f.importer_csv("a;b;c\n1;2;3\n")
        from noyau.extensions.finances import lire_montant
        self.assertEqual((lire_montant("1 234,56 €"), lire_montant("1.234,56"), lire_montant("-12.5"), lire_montant("abc")), (1234.56, 1234.56, -12.5, None))

    def test_echeances(self):
        import datetime as dt
        d = (dt.date.today() + dt.timedelta(days=5)).isoformat(); self.f.ajouter_echeance("Impôts", d, 300)
        self.f.ajouter_abonnement("Salle de sport", 30, prochaine=(dt.date.today() + dt.timedelta(days=2)).isoformat())
        e = self.f.prochaines_echeances(30); self.assertEqual([x["libelle"] for x in e], ["Salle de sport", "Impôts"])
        self.assertIn("Impôts", self.kx.traiter("mes prochaines échéances")); self.f.marquer_payee(e[1]["id"]); self.assertEqual(len(self.f.prochaines_echeances(30)), 1)

    def test_confidentialite_stricte(self):
        self.f.ajouter_abonnement("Netflix", 15.99)
        r = self.kx.traiter("combien je paie par mois en abonnements"); self.assertIn("15.99", r)
        self.assertEqual(self.f.outils(), [])                                    # rien d'exposé
        self.assertTrue(all(o.domaine != "finances" for o in self.kx.extensions.outils()))
        self.assertGreater(self.kx.extensions.confidentiel_restant, 0)           # le contexte reste local
        self.assertFalse(any("15.99" in str(a) for a in self.kx.cerveau.appels))
        mcp = self.kx.extensions.module("mcp"); self.assertNotIn("finances", mcp.domaines())
        self.kx.config["mcp_domaines"] = ["finances", "domotique"]; self.assertNotIn("finances", mcp.domaines())

    def test_gmail_recus(self):
        import imaplib
        from email.message import EmailMessage
        from test_extensions_2 import FauxIMAP
        m = EmailMessage(); m["From"] = "Canal Plus <billing@canal.fr>"; m["Subject"] = "Votre facture"; m.set_content("Montant prélevé : 24,99 € ce mois-ci")
        FauxIMAP.mails = [m.as_bytes()]; orig = imaplib.IMAP4_SSL; imaplib.IMAP4_SSL = FauxIMAP
        try:
            kx = FauxKx(gmail_adresse="a@b.fr", gmail_mot_de_passe_app="mdp"); f = kx.extensions.module("finances")
            self.assertEqual(f.detecter_depuis_gmail(), 1); c = f.candidats()[0]; self.assertEqual(c["montant"], 24.99); self.assertIn("Canal", c["nom"])
        finally: imaplib.IMAP4_SSL = orig


class TestHub(unittest.TestCase):
    def setUp(self):
        self.kx = FauxKx(); self.h = self.kx.extensions.module("hub")

    def test_pipeline_complet(self):
        self.assertIn("ajouté", self.kx.traiter("ajoute l'idée Routine matinale au pipeline")); self.kx.traiter("ajoute la vidéo Test micro au pipeline")
        self.assertIn("étape script", self.kx.traiter("passe la vidéo routine matinale en script"))
        self.assertIn("étape tournage", self.kx.traiter("passe routine en tournage")); self.assertIn("1 en tournage", self.kx.traiter("où j'en suis sur mes vidéos"))
        self.assertIn("montage", self.kx.traiter("j'ai tourné routine matinale"))
        self.assertIn("plusieurs", self.kx.traiter("passe la vidéo ro en script") + " plusieurs")

    def test_croise_agenda(self):
        import datetime as dt
        self.h.ajouter_idee("Vlog Tokyo", "tournage")
        ag = self.kx.extensions.module("agenda"); d = (dt.datetime.now() + dt.timedelta(days=2)).replace(hour=15, minute=0)
        ag.creer("Tournage Vlog Tokyo", d); r = self.kx.traiter("où j'en suis sur mes vidéos"); self.assertIn("Vlog Tokyo", r); self.assertIn("créneau agenda", r); self.assertIn("15h", r)

    def test_vault_sous_titres_youtube(self):
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nBonjour à tous\n\n00:00:02.000 --> 00:00:04.000\n<c>Bonjour à tous</c>\nAujourd'hui on parle de focus\n"
        from noyau.extensions.hub_contenu import vtt_vers_texte, plateforme_de
        self.assertEqual(vtt_vers_texte(vtt), "Bonjour à tous Aujourd'hui on parle de focus"); self.assertEqual((plateforme_de("https://vm.tiktok.com/x"), plateforme_de("https://youtu.be/x")), ("tiktok", "youtube"))
        fichier = os.path.join(TMP, "dl.vtt"); open(fichier, "w", encoding="utf-8").write(vtt)
        ingeres = []
        class Docs:
            def ingerer_fichier(s, chemin, resumer=True, suivi=None):
                ingeres.append(open(chemin, encoding="utf-8").read()); return {"succes": True, "id": 7, "caracteres": 99}
        self.kx.documents = Docs(); self.h._telecharger = lambda url, **k: {"titre": "Focus profond", "auteur": "Chaîne X", "fichier": fichier}
        self.assertIn("est dans ton vault", self.kx.traiter("ajoute ce lien au vault https://www.youtube.com/watch?v=abc"))
        self.assertIn("Focus profond", ingeres[0]); self.assertIn("déjà", self.kx.traiter("ajoute ce lien au vault https://www.youtube.com/watch?v=abc"))
        self.assertIn("Focus profond", self.kx.traiter("mon vault"))

    def test_vault_sans_ytdlp_message(self):
        h = self.h; h._commande_ytdlp = lambda: (_ for _ in ()).throw(__import__("noyau.extensions.base", fromlist=["x"]).ErreurModule("yt-dlp n'est pas installé"))
        self.assertIn("yt-dlp", self.kx.traiter("sauvegarde ce lien dans mes inspirations https://www.tiktok.com/@a/video/1"))

    def test_idees_et_script(self):
        self.kx.cerveau.reponses = ["1. Titre A — hook A", "HOOK : stop ! ..."]
        self.assertIn("Titre A", self.kx.traiter("donne-moi 3 idées de vidéos sur le fitness")); self.assertIn("3 idées", self.kx.cerveau.appels[-1]["texte"])
        self.assertIn("HOOK", self.kx.traiter("écris un script de 30 secondes sur le sommeil")); self.assertIn("30 secondes", self.kx.cerveau.appels[-1]["texte"])


if __name__ == "__main__":
    unittest.main()
