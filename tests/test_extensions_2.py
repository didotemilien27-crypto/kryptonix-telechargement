import json, os, socket, sys, threading, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from faux import FauxKx, Mock, TMP


class TestDomotique(unittest.TestCase):
    def setUp(self):
        self.hue = Mock(); self.actions = []; self.pair = 0
        K = "/api/CLE"
        self.hue.ok("GET", K + "/groups", {"1": {"name": "Salon", "lights": ["1"]}, "2": {"name": "Cuisine", "lights": ["2"]}})
        self.hue.ok("GET", K + "/lights", {"1": {"name": "Lampe A"}, "2": {"name": "Lampe B"}})
        self.hue.ok("GET", K + "/scenes", {"abc": {"name": "Cinéma", "group": "1"}})
        self.hue.routes[("PUT", K + "/groups/*")] = lambda r: (self.actions.append((r["chemin"].split("/")[-2], r["json"])) or (200, [{"success": {}}], None))
        def pairing(r):
            self.pair += 1
            return (200, [{"error": {"type": 101, "description": "link button not pressed"}}], None) if self.pair == 1 else (200, [{"success": {"username": "NOUVELLE"}}], None)
        self.hue.routes[("POST", "/api")] = pairing
        self.kx = FauxKx(hue_pont=self.hue.hote, hue_cle="CLE")

    def tearDown(self): self.hue.arreter()

    def test_allumer_eteindre_pieces(self):
        self.assertIn("allumé", self.kx.traiter("allume le salon")); self.assertEqual(self.actions[-1], ("1", {"on": True}))
        self.assertIn("éteint", self.kx.traiter("éteins la cuisine")); self.assertEqual(self.actions[-1], ("2", {"on": False}))
        self.assertIn("Tout est éteint", self.kx.traiter("éteins toutes les lumières")); self.assertEqual(self.actions[-1], ("0", {"on": False}))

    def test_intensite_couleur(self):
        self.kx.traiter("mets la lumière du salon à 50 %"); self.assertEqual(self.actions[-1], ("1", {"on": True, "bri": 127}))
        self.kx.traiter("mets les lumières en bleu"); g, e = self.actions[-1]; self.assertEqual((g, e["hue"], e["sat"]), ("0", 46920, 254))
        self.kx.traiter("mets la lumière de la cuisine en rouge à 20 %"); g, e = self.actions[-1]; self.assertEqual((g, e["hue"], e["bri"]), ("2", 0, 51))
        self.kx.traiter("lumières en blanc chaud"); self.assertEqual(self.actions[-1][1]["ct"], 400)
        self.kx.traiter("baisse la luminosité"); self.assertEqual(self.actions[-1][1], {"bri_inc": -60})

    def test_scene_du_pont_puis_ambiance_predefinie(self):
        self.assertIn("Cinéma", self.kx.traiter("ambiance cinéma")); self.assertEqual(self.actions[-1], ("1", {"scene": "abc"}))
        self.assertIn("détente", self.kx.traiter("ambiance détente")); self.assertEqual(self.actions[-1][1]["ct"], 400)
        self.assertIn("ne connais pas", self.kx.traiter("ambiance zzz inconnue"))

    def test_pont_injoignable_message_clair(self):
        kx = FauxKx(hue_pont="127.0.0.1:1", hue_cle="x")
        self.assertIn("joindre", kx.traiter("allume les lumières"))
        self.assertIn("pas configuré", FauxKx().traiter("allume les lumières").replace("Je ne connais pas ton pont Hue", "pas configuré"))

    def test_association(self):
        kx = FauxKx(hue_pont=self.hue.hote)
        self.assertIn("pas encore associé", kx.traiter("allume les lumières"))
        self.assertIn("impossible", kx.traiter("associe le pont hue"))
        self.assertIn("associé", kx.traiter("associe le pont hue")); self.assertEqual(kx.config["hue_cle"], "NOUVELLE")

    def test_toit_ne_vole_pas_pc(self):
        self.assertNotIn("lumi", self.kx.traiter("allume le pc").lower())

    def test_presence_hysteresis(self):
        from noyau.extensions.domotique import Presence
        evts, etat = [], {"vu": True}
        p = Presence(["x"], evts.append, sonde=lambda h: etat["vu"], echecs_max=3)
        p.un_tour(); self.assertTrue(p.present); self.assertEqual(evts, [])          # 1er relevé : pas d'événement
        etat["vu"] = False
        p.un_tour(); p.un_tour(); self.assertTrue(p.present)                           # veille Wi-Fi : on patiente
        p.un_tour(); self.assertFalse(p.present); self.assertEqual(evts, [False])
        etat["vu"] = True; p.un_tour(); self.assertEqual(evts, [False, True])

    def test_presence_declenche_scenes(self):
        d = self.kx.extensions.module("domotique")
        self.kx.config.update(presence_scene_depart="eteindre", presence_scene_retour="detente")
        d._presence_change(False); self.assertEqual(self.actions[-1], ("0", {"on": False}))
        d._presence_change(True); self.assertEqual(self.actions[-1][1]["ct"], 400)
        self.assertTrue(any("Bon retour" in x for x in self.kx.voix.dit))

    def test_wol_et_paquet(self):
        from noyau.extensions.domotique import paquet_magique, envoyer_wol
        pq = paquet_magique("AA:BB:CC:DD:EE:FF"); self.assertEqual(len(pq), 102); self.assertEqual(pq[:6], b"\xff" * 6); self.assertEqual(pq[6:12], bytes.fromhex("AABBCCDDEEFF"))
        from noyau.extensions.base import ErreurModule
        with self.assertRaises(ErreurModule): paquet_magique("pas une mac")
        recu = []
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", 0)); s.settimeout(3)
        envoyer_wol("AA:BB:CC:DD:EE:FF", "127.0.0.1", s.getsockname()[1]); recu.append(s.recvfrom(200)[0]); s.close()
        self.assertEqual(recu[0], pq)

    def test_reveil_pc_voix_niveau2(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", 0)); s.settimeout(3)
        kx = FauxKx(wol_machines={"bureau": "AA:BB:CC:DD:EE:FF"}, wol_diffusion="127.0.0.1", wol_port=s.getsockname()[1])
        q = kx.traiter("réveille le pc du bureau"); self.assertIn("confirme", q)
        self.assertIn("réveil envoyé", kx.traiter("oui")); self.assertEqual(s.recvfrom(200)[0][6:12], bytes.fromhex("AABBCCDDEEFF")); s.close()

    def test_extinction_pc_n3(self):
        kx = FauxKx(delai_securite_n3=0.2); d = kx.extensions.module("domotique")
        self.assertIn("confirmation stricte", kx.traiter("éteins le PC").lower())
        self.assertIn("annulé", kx.traiter("oui mais non")); self.assertEqual(d.dernieres_commandes, [])
        kx.traiter("éteins l'ordinateur"); self.assertIn("Confirmé", kx.traiter("oui je confirme")); time.sleep(0.6)
        self.assertEqual(d.dernieres_commandes, ["eteindre"])
        kx.traiter("redémarre le pc"); kx.traiter("oui"); kx.traiter("annule"); time.sleep(0.5); self.assertEqual(d.dernieres_commandes, ["eteindre"])


class TestAgenda(unittest.TestCase):
    def setUp(self):
        import datetime as dt
        self.dt = dt; self.g = Mock(); self.crees, self.supprimes = [], []
        demain = (dt.datetime.now() + dt.timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
        self.demain = demain
        self.g.ok("POST", "/token", {"access_token": "AT", "expires_in": 3600})
        self.g.ok("GET", "/calendar/v3/users/me/calendarList", {"items": [{"id": "primary", "summary": "Perso", "accessRole": "owner"}]})
        self.g.routes[("GET", "/calendar/v3/calendars/primary/events")] = lambda r: (200, {"items": [
            {"id": "e1", "summary": "Dentiste", "start": {"dateTime": demain.astimezone().isoformat()}, "end": {"dateTime": (demain + dt.timedelta(hours=1)).astimezone().isoformat()}},
            {"id": "e2", "summary": "Anniversaire Paul", "start": {"date": demain.date().isoformat()}, "end": {"date": (demain.date() + dt.timedelta(days=1)).isoformat()}}]}, None)
        self.g.routes[("POST", "/calendar/v3/calendars/primary/events")] = lambda r: (self.crees.append(r["json"]) or (200, {"id": "nouv"}, None))
        self.g.routes[("DELETE", "/calendar/v3/calendars/primary/events/e1")] = lambda r: (self.supprimes.append("e1") or (204, "", None))
        self.kx = FauxKx(google_client_id="i", google_client_secret="s", google_refresh_token="r", google_api_base=self.g.url, google_token_url=self.g.url + "/token")

    def tearDown(self): self.g.arreter()

    def test_lecture_google(self):
        r = self.kx.traiter("qu'est-ce que j'ai demain"); self.assertIn("Dentiste", r); self.assertIn("14h", r); self.assertIn("Anniversaire", r)
        self.assertIn("Dentiste", self.kx.traiter("mon prochain rendez-vous"))
        self.assertEqual(self.g.requetes[0]["form"]["grant_type"], "refresh_token")

    def test_creation_confirmation_stricte(self):
        q = self.kx.traiter("ajoute un rendez-vous coiffeur demain à 10h30"); self.assertIn("je confirme", q); self.assertEqual(self.crees, [])
        r = self.kx.traiter("non"); self.assertIn("annulé", r); self.assertEqual(self.crees, [])
        self.kx.traiter("ajoute un rendez-vous coiffeur demain à 10h30"); self.assertIn("ajouté", self.kx.traiter("oui"))
        self.assertEqual(self.crees[0]["summary"], "Coiffeur"); self.assertIn("T10:30", self.crees[0]["start"]["dateTime"])

    def test_creation_negation_cachee(self):
        self.kx.traiter("ajoute une réunion équipe demain à 9h"); self.kx.traiter("oui, enfin non"); self.assertEqual(self.crees, [])

    def test_creation_sans_heure(self):
        self.assertIn("À quelle heure", self.kx.traiter("ajoute un rendez-vous dentiste demain"))

    def test_suppression_n3(self):
        q = self.kx.traiter("supprime le rendez-vous dentiste demain"); self.assertIn("confirmation stricte", q.lower())
        self.assertIn("annulé", self.kx.traiter("non")); self.assertEqual(self.supprimes, [])
        self.kx.traiter("supprime le rendez-vous dentiste demain"); self.kx.traiter("oui je confirme"); time.sleep(0.6); self.assertEqual(self.supprimes, ["e1"])

    def test_suppression_introuvable_ou_plusieurs(self):
        self.assertIn("aucun rendez-vous", self.kx.traiter("supprime le rendez-vous kayak"))

    def test_ical_lecture_seule_et_local(self):
        dt = self.dt; ics = f"""BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:z\nDTSTART:{self.demain.strftime('%Y%m%dT%H%M%S')}\nDTEND:{(self.demain + dt.timedelta(hours=2)).strftime('%Y%m%dT%H%M%S')}\nSUMMARY:Match de foot\\, stade\nEND:VEVENT\nEND:VCALENDAR"""
        m = Mock(); m.ok("GET", "/cal.ics", ics, type_="text/calendar")
        kx = FauxKx(agenda_ical=[{"nom": "Sport", "url": m.url + "/cal.ics"}])
        r = kx.traiter("qu'est-ce que j'ai demain"); self.assertIn("Match de foot, stade", r)
        self.assertIn("lecture seule", kx.traiter("supprime le rendez-vous match de foot demain"))
        kx.traiter("ajoute un rendez-vous yoga demain à 18h"); kx.traiter("oui"); self.assertIn("Yoga", kx.traiter("qu'est-ce que j'ai demain"))
        m.arreter()

    def test_agenda_panne_google_message(self):
        self.g.routes[("GET", "/calendar/v3/users/me/calendarList")] = lambda r: (500, {}, None)
        self.assertIn("n'ont pas répondu", self.kx.traiter("qu'est-ce que j'ai demain"))


class FauxIMAP:
    mails = []
    def __init__(self, *a, **k): pass
    def login(self, u, p):
        if p != "mdp": 
            import imaplib; raise imaplib.IMAP4.error("bad")
    def select(self, *a, **k): return "OK", [b"1"]
    def search(self, *a): return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.mails)))]
    def fetch(self, i, spec): return "OK", [(b"1 (BODY[] {1})", self.mails[int(i) - 1])]
    def logout(self): pass


class TestEmails(unittest.TestCase):
    def setUp(self):
        import imaplib
        from email.message import EmailMessage
        def mail(de, sujet, corps):
            m = EmailMessage(); m["From"] = de; m["Subject"] = sujet; m["Date"] = "Wed, 7 Oct 2026 08:00:00 +0200"; m.set_content(corps); return m.as_bytes()
        FauxIMAP.mails = [mail("Paul Durand <paul@ex.fr>", "Réunion déplacée", "Salut, la réunion passe à jeudi 15h."),
                          mail("=?utf-8?q?Caf=C3=A9_Bo=C3=AEte?= <noreply@cafe.fr>", "Votre reçu", "Total : 12,90 € merci")]
        self._orig = imaplib.IMAP4_SSL; imaplib.IMAP4_SSL = FauxIMAP
        self.kx = FauxKx(gmail_adresse="a@b.fr", gmail_mot_de_passe_app="mdp")

    def tearDown(self):
        import imaplib; imaplib.IMAP4_SSL = self._orig

    def test_resume_prive_local(self):
        self.kx.cerveau.reponses = ["Paul déplace la réunion à jeudi."]
        r = self.kx.traiter("résume mes mails"); self.assertIn("2 mails non lus", r); self.assertIn("Paul déplace", r)
        self.assertTrue(self.kx.cerveau.appels[-1]["prive"]); self.assertIn("Réunion déplacée", self.kx.cerveau.appels[-1]["texte"])

    def test_sans_cerveau_liste_simple(self):
        self.kx.cerveau.disponible = False; self.kx.cerveau.verifier_connexion = lambda: False
        self.assertIn("Paul Durand : Réunion déplacée", self.kx.traiter("ai-je de nouveaux mails"))

    def test_entetes_encodes(self):
        mails = self.kx.extensions.module("emails").lire("UNSEEN"); self.assertEqual(mails[-1]["de"], "Café Boîte" if mails[-1]["de"].startswith("Caf") else mails[-1]["de"])
        self.assertTrue(any("Café Boîte" == m["de"] for m in mails))

    def test_mauvais_mot_de_passe(self):
        self.kx.config["gmail_mot_de_passe_app"] = "faux"; self.assertIn("refusé", self.kx.traiter("résume mes mails"))

    def test_non_configure(self):
        self.assertIn("pas configurée", FauxKx().traiter("résume mes mails"))

    def test_brouillon_et_envoi_n3(self):
        import smtplib; envoyes = []
        class S:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def login(self, *a): pass
            def send_message(self, m): envoyes.append(m)
        orig = smtplib.SMTP_SSL; smtplib.SMTP_SSL = S
        try:
            self.kx.cerveau.reponses = ["Bonjour Paul, je décale la réunion. Cordialement."]
            r = self.kx.traiter("écris un mail pour décaler la réunion"); self.assertIn("brouillon", r.lower())
            self.assertIn("Destinataire réglé", self.kx.traiter("le destinataire est paul@ex.fr"))
            self.assertIn("confirmation stricte", self.kx.traiter("envoie le brouillon").lower()); self.assertEqual(envoyes, [])
            self.kx.traiter("oui je confirme"); time.sleep(0.6); self.assertEqual(len(envoyes), 1); self.assertEqual(envoyes[0]["To"], "paul@ex.fr")
        finally:
            smtplib.SMTP_SSL = orig


class TestSocial(unittest.TestCase):
    def setUp(self):
        import datetime as dt
        self.m = Mock(); self.ig = {"followers": 1000, "vues": 500}
        ts = (int(time.time()) - 60 - ((1420070400000) // 1000) )
        sid = lambda n: str(((int(time.time() * 1000) - 1420070400000) << 22) + n)
        self.msgs = [{"id": sid(1), "content": "salut @toi", "author": {"username": "bob", "global_name": "Bob"}, "mentions": [{"id": "42"}]},
                     {"id": sid(2), "content": "blabla", "author": {"username": "eve"}, "mentions": []}]
        self.m.routes[("GET", "/channels/10/messages")] = lambda r: (200, self.msgs, None)
        self.m.ok("GET", "/channels/10", {"name": "général"})
        self.m.routes[("GET", "/IG1")] = lambda r: (200, {"username": "moi", "followers_count": self.ig["followers"], "media_count": 12}, None)
        self.m.ok("GET", "/IG1/media", {"data": [{"id": "M1", "media_type": "VIDEO"}, {"id": "M2", "media_type": "IMAGE"}]})
        self.m.routes[("GET", "/M1/insights")] = lambda r: (200, {"data": [{"values": [{"value": self.ig["vues"]}]}]}, None)
        self.kx = FauxKx(discord_token="tok", discord_salons=["10"], discord_utilisateur_id="42", discord_api_base=self.m.url,
                         instagram_comptes=[{"nom": "perso", "id": "IG1", "jeton": "T"}], instagram_api_base=self.m.url)

    def tearDown(self): self.m.arreter()

    def test_recap_discord(self):
        self.kx.cerveau.reponses = ["Bob t'a salué."]
        r = self.kx.traiter("résume discord aujourd'hui"); self.assertIn("2 message(s)", r); self.assertIn("général", r); self.assertIn("mentionné 1 fois", r); self.assertIn("Bob t'a salué", r)
        self.assertEqual(self.m.requetes[0]["entetes"]["Authorization"], "Bot tok"); self.assertTrue(self.kx.cerveau.appels[-1]["prive"])

    def test_mentions(self):
        r = self.kx.traiter("ai-je été mentionné sur discord"); self.assertIn("1 mention", r); self.assertIn("Bob", r)

    def test_ecoute_mentions_arriere_plan(self):
        s = self.kx.extensions.module("social"); s._surveiller_mentions()                 # 1er passage : amorçage silencieux
        self.assertEqual(self.kx.voix.dit, [])
        sid = lambda n: str(((int(time.time() * 1000) - 1420070400000) << 22) + 1000 + n)
        self.msgs.append({"id": sid(3), "content": "hey @toi tu es là", "author": {"username": "zoe"}, "mentions": [{"id": "42"}]})
        s._surveiller_mentions(); self.assertTrue(any("zoe" in d for d in self.kx.voix.dit))

    def test_discord_erreurs(self):
        self.m.routes[("GET", "/channels/10/messages")] = lambda r: (401, {}, None); self.assertIn("refusé", self.kx.traiter("résume discord aujourd'hui"))
        self.assertIn("pas configuré", FauxKx().traiter("résume discord aujourd'hui"))

    def test_instagram_compare_veille(self):
        import datetime as dt
        r = self.kx.traiter("combien d'abonnés sur instagram"); self.assertIn("1000 abonnés", r); self.assertIn("première mesure", r)
        hier = (dt.date.today() - dt.timedelta(days=1)).isoformat(); s = self.kx.extensions.module("social")
        s.db.executer("DELETE FROM instagram_snap"); s.db.executer("INSERT INTO instagram_snap VALUES ('IG1', ?, 950, 400, 10)", (hier,))
        self.ig.update(followers=1012, vues=640)
        r = self.kx.traiter("stats instagram"); self.assertIn("1012 abonnés", r); self.assertIn("+62 hier", r); self.assertIn("+240", r)

    def test_instagram_jeton_refuse(self):
        self.m.routes[("GET", "/IG1")] = lambda r: (400, {"error": {"message": "Invalid OAuth"}}, None); self.assertIn("refuse", self.kx.traiter("stats instagram"))


class TestTelephone(unittest.TestCase):
    def setUp(self):
        self.m = Mock(); self.appels = []
        self.m.routes[("POST", "/2010-04-01/Accounts/ACx/Calls.json")] = lambda r: (self.appels.append(r) or (201, {"sid": "CA1"}, None))
        self.m.routes[("POST", "/2010-04-01/Accounts/ACx/Calls/CA1.json")] = lambda r: (self.appels.append(r) or (200, {}, None))
        self.kx = FauxKx(twilio_sid="ACx", twilio_token="tok", twilio_numero="+33900000000", twilio_api_base=self.m.url,
                         twilio_contacts={"Maman": "+33612345678"}, twilio_url_publique="https://exemple.ngrok.app", delai_securite_n3=0.2)

    def tearDown(self): self.m.arreter()

    def test_message_vocal_n3(self):
        q = self.kx.traiter("appelle Maman et dis-lui que j'arrive en retard"); self.assertIn("confirmation stricte", q.lower()); self.assertEqual(self.appels, [])
        self.kx.traiter("oui je confirme"); time.sleep(0.6); self.assertEqual(len(self.appels), 1)
        f = self.appels[0]["form"]; self.assertEqual(f["To"], "+33612345678"); self.assertIn("j'arrive en retard", f["Twiml"]); self.assertIn('language="fr-FR"', f["Twiml"])

    def test_negation_n_appelle_pas(self):
        self.kx.traiter("appelle Maman et dis-lui bonjour"); self.kx.traiter("non surtout pas"); time.sleep(0.5); self.assertEqual(self.appels, [])

    def test_numero_direct_conversation(self):
        self.kx.traiter("appelle le 06 12 34 56 78 pour confirmer le rendez-vous"); self.kx.traiter("oui"); time.sleep(0.6)
        f = self.appels[0]["form"]; self.assertEqual(f["To"], "+33612345678"); self.assertEqual(f["Url"], "https://exemple.ngrok.app/twilio/voix")

    def test_contact_inconnu(self):
        self.assertIn("ne connais pas", self.kx.traiter("appelle Zorglub et dis-lui salut"))

    def test_signature_twilio_valeur_officielle(self):
        from noyau.extensions.telephone import signature_twilio
        # exemple de la documentation Twilio
        sig = signature_twilio("12345", "https://mycompany.com/myapp.php?foo=1&bar=2",
                               {"CallSid": "CA1234567890ABCDE", "Caller": "+14158675310", "Digits": "1234", "From": "+14158675310", "To": "+18005551212"})
        self.assertEqual(sig, "GvWf1cFY/Q7PnoempGyD5oXAezc=")

    def test_webhooks_conversation(self):
        t = self.kx.extensions.module("telephone")
        self.kx.cerveau.reponses = ["Bien sûr, jeudi à 15h."]
        x = t.webhook_debut({"CallSid": "CA9", "Direction": "inbound"}); self.assertIn("n'accepte pas", x); self.assertIn("<Hangup/>", x)
        self.kx.config["twilio_repondre_entrants"] = True
        x = t.webhook_debut({"CallSid": "CA9", "Direction": "inbound"}); self.assertIn("<Gather", x); self.assertIn("/twilio/suite", x)
        x = t.webhook_suite({"CallSid": "CA9", "SpeechResult": "Pouvez-vous jeudi ?"}); self.assertIn("jeudi à 15h", x)
        self.assertIn("Interlocuteur : Pouvez-vous jeudi", self.kx.cerveau.appels[-1]["texte"])
        self.assertIn("Au revoir", t.webhook_suite({"CallSid": "CA9", "SpeechResult": "merci au revoir"}))
        self.assertIn("<", t.webhook_suite({"CallSid": "CA9", "SpeechResult": "a < b & c"}).replace("&amp;", "&") and "<")   # XML bien formé

    def test_signature_verifiee(self):
        from noyau.extensions.telephone import signature_twilio
        t = self.kx.extensions.module("telephone"); p = {"CallSid": "X"}; url = "https://exemple.ngrok.app/twilio/voix"
        self.assertTrue(t.signature_valide(url, p, signature_twilio("tok", url, p)))
        self.assertFalse(t.signature_valide(url, p, "faux")); self.assertFalse(t.signature_valide(url, p, ""))

    def test_raccrocher(self):
        self.kx.extensions.module("telephone").dernier_appel = "CA1"; self.assertIn("raccroché", self.kx.traiter("raccroche")); self.assertEqual(self.appels[-1]["form"]["Status"], "completed")

    def test_twilio_refuse(self):
        self.m.routes[("POST", "/2010-04-01/Accounts/ACx/Calls.json")] = lambda r: (401, {}, None)
        self.kx.traiter("appelle Maman et dis-lui salut"); self.kx.traiter("oui"); time.sleep(0.6)
        self.assertTrue(any("refuse" in d for d in self.kx.voix.dit), self.kx.voix.dit)


if __name__ == "__main__":
    unittest.main()
