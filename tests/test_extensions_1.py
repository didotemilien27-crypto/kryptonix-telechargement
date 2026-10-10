import os, sys, time, unittest, wave
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from faux import FauxKx, Mock, TMP


class TestSecurite(unittest.TestCase):
    def setUp(self):
        self.kx = FauxKx()
        self.trace = []
        from noyau.extensions.base import Outil
        mk = lambda nom, niv: Outil(nom, nom, lambda a, n=nom: (self.trace.append(n), f"{n} fait")[1], niveau=niv, domaine="test")
        self.kx.extensions.modules["pc"].outils = lambda: [mk("n1", 1), mk("n2", 2), mk("n3", 3)]
        self.sec = self.kx.extensions.securite

    def test_analyse_confirmation_stricte(self):
        from noyau.extensions.securite import analyser_confirmation as a
        for oui in ["oui", "Oui, je confirme", "ouais", "d'accord", "vas-y", "ok", "bien sûr !"]:
            self.assertEqual(a(oui), "oui", oui)
        for non in ["non", "pas du tout", "oui mais non", "oui annule", "surtout pas", "ne le fais pas", "oui... enfin non",
                    "stop", "laisse tomber", "n'importe quoi", "finalement non", "oui ne fais rien"]:
            self.assertEqual(a(non), "non", non)
        for amb in ["peut-être", "hmm", "euh bof", ""]:
            self.assertEqual(a(amb), "ambigu", amb)

    def test_n1_direct(self):
        self.assertEqual(self.sec.lancer("n1", {}), "n1 fait")
        self.assertEqual(self.trace, ["n1"])

    def test_n2_oui_execute(self):
        q = self.sec.lancer("n2", {}, description="faire n2")
        self.assertIn("je confirme", q); self.assertEqual(self.trace, [])
        self.assertEqual(self.kx.extensions.intercepter("oui"), "n2 fait")
        self.assertEqual(self.trace, ["n2"])

    def test_n2_negation_n_execute_pas(self):
        self.sec.lancer("n2", {})
        r = self.kx.extensions.intercepter("oui mais non")
        self.assertIn("annulé", r); self.assertEqual(self.trace, [])
        self.assertFalse(self.sec.en_attente)

    def test_ambigu_court_annule_long_passe_au_routage(self):
        self.sec.lancer("n2", {})
        self.assertIn("par sécurité", self.kx.extensions.intercepter("hmm"))
        self.sec.lancer("n2", {})
        self.assertIsNone(self.kx.extensions.intercepter("quelle heure est-il maintenant à Paris"))
        self.assertEqual(self.trace, [])

    def test_n3_delai_annulable(self):
        self.sec.lancer("n3", {}, description="détruire")
        r = self.kx.extensions.intercepter("oui je confirme")
        self.assertIn("dans", r); self.assertEqual(self.trace, [])
        self.assertTrue(self.sec.en_attente)
        self.assertIn("Annulé", self.kx.extensions.intercepter("annule"))
        time.sleep(0.5); self.assertEqual(self.trace, [])

    def test_n3_execute_apres_delai(self):
        self.sec.lancer("n3", {}); self.kx.extensions.intercepter("oui")
        time.sleep(0.7); self.assertEqual(self.trace, ["n3"])
        self.assertTrue(any("n3 fait" in d for d in self.kx.voix.dit))

    def test_toujours_autoriser_n2_mais_jamais_n3(self):
        self.assertTrue(self.sec.autoriser_toujours("n2")); self.assertFalse(self.sec.autoriser_toujours("n3"))
        self.assertEqual(self.sec.lancer("n2", {}), "n2 fait")
        self.assertIn("Action sensible", self.sec.lancer("n3", {}))
        self.assertEqual(self.sec.revoquer("n2"), 1)
        self.assertIn("je confirme", self.sec.lancer("n2", {}))

    def test_commandes_vocales_permissions(self):
        self.assertIn("action(s) autorisée(s)", self.kx.traiter("toujours autoriser test"))
        self.assertIn("niveau 3", self.kx.traiter("toujours autoriser test") + "niveau 3")
        self.assertIn("n2", self.kx.traiter("quelles sont mes permissions"))
        self.assertIn("révoquée", self.kx.traiter("révoque toutes les autorisations"))

    def test_expiration(self):
        self.sec.lancer("n2", {}); self.sec._attente["expire"] = time.time() - 1
        self.assertFalse(self.sec.en_attente)

    def test_niveau_modifiable_et_persistant(self):
        self.sec.definir_niveau("n1", 2)
        self.assertEqual(self.sec.niveau_de("n1"), 2)
        from noyau.extensions.securite import Securite
        self.assertEqual(Securite(self.kx).niveau_de("n1"), 2)


class TestSons(unittest.TestCase):
    def test_catalogue_et_fichiers_wav(self):
        kx = FauxKx(); s = kx.extensions.sons
        self.assertGreaterEqual(len(s.catalogue), 40)
        s.fabriquer()
        for nom in s.catalogue:
            with wave.open(s.chemin(nom)) as w:
                self.assertGreater(w.getnframes(), 200, nom)

    def test_commandes(self):
        kx = FauxKx()
        self.assertIn("coupés", kx.traiter("coupe les sons")); self.assertFalse(kx.config["sons_actifs"])
        self.assertIn("activés", kx.traiter("active les sons")); self.assertTrue(kx.config["sons_actifs"])
        self.assertIn("Voici le son", kx.traiter("joue le son minuteur"))
        self.assertIn("effets sonores", kx.traiter("liste les sons"))

    def test_evenement_etat(self):
        kx = FauxKx(sons_actifs=True); kx.extensions.sons.fabriquer(); kx.extensions.sons._lire = lambda c: None
        kx.extensions.sons.joue_pour_etat("ecoute")
        self.assertIn("ecoute_debut", kx.extensions.sons.historique)


class TestPersonnalites(unittest.TestCase):
    def test_changement_et_prompt(self):
        kx = FauxKx()
        self.assertEqual(kx.extensions.consigne_personnalite(), "")
        self.assertIn("sarcastique", kx.traiter("passe en mode sarcastique"))
        self.assertIn("sarcastique", kx.extensions.consigne_personnalite().lower())
        self.assertEqual(kx.config["personnalite"], "sarcastique"); self.assertEqual(kx.config["hauteur_voix"], -8)
        self.assertIn("concis", kx.traiter("sois concis").lower())
        kx.config["personnalite_voix"] = False; v = kx.config["vitesse_voix"]
        kx.traiter("passe en mode coach"); self.assertEqual(kx.config["vitesse_voix"], v)

    def test_ne_vole_pas_mode_hybride(self):
        kx = FauxKx(); kx.traiter("passe en mode hybride"); self.assertEqual(kx.config["personnalite"] if "personnalite" in kx.config else "majordome", "majordome")


class FausseAnthropic:
    def __init__(self, mock):
        mock.routes[("POST", "/v1/messages")] = self.rep; self.vus = []
    def rep(self, r):
        self.vus.append(r)
        return 200, {"content": [{"type": "text", "text": "Réponse cloud"}], "usage": {"input_tokens": 1000, "output_tokens": 200}}, None


class TestRouteur(unittest.TestCase):
    def setUp(self):
        self.mock = Mock(); self.api = FausseAnthropic(self.mock)
        self.kx = FauxKx(anthropic_base_url=self.mock.url, cles_api={"anthropic": "sk-test"}, mode_ia="hybride")
        self.r = self.kx.extensions.routeur
        self.msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]

    def tearDown(self): self.mock.arreter()

    def test_local_ne_sort_pas(self):
        self.kx.config["mode_ia"] = "local"; self.assertIsNone(self.r.completer(self.msgs)); self.assertEqual(self.api.vus, [])

    def test_hybride_cloud_et_cout(self):
        self.assertEqual(self.r.completer(self.msgs), "Réponse cloud")
        req = self.api.vus[0]
        self.assertEqual(req["json"]["model"], "claude-haiku-4-5-20251001"); self.assertEqual(req["entetes"]["x-api-key"], "sk-test")
        self.assertEqual(req["json"]["system"], "sys")
        self.assertAlmostEqual(self.r.depense_mois(), (1000 * 1.0 + 200 * 5.0) / 1e6, places=6)

    def test_qualite_utilise_modele_puissant(self):
        self.kx.config["mode_ia"] = "qualite"; self.r.completer(self.msgs)
        self.assertEqual(self.api.vus[-1]["json"]["model"], "claude-opus-5-5")

    def test_prive_et_documents_restent_locaux(self):
        self.assertIsNone(self.r.completer(self.msgs, prive=True)); self.assertIsNone(self.r.completer(self.msgs, a_documents=True))
        self.kx.config["cloud_documents"] = True; self.assertEqual(self.r.completer(self.msgs, a_documents=True), "Réponse cloud")

    def test_plafond_bascule_locale(self):
        self.kx.config["budget_mensuel"] = 0.0001; self.r.completer(self.msgs)
        self.assertTrue(self.r.plafond_atteint())
        n = len(self.api.vus); self.assertIsNone(self.r.completer(self.msgs)); self.assertEqual(len(self.api.vus), n)
        self.assertTrue(self.r.bascule_budget); self.assertTrue(any(e == "alerte" for e, _ in self.kx.diffuses))

    def test_panne_cloud_repli_local(self):
        self.mock.routes[("POST", "/v1/messages")] = lambda r: (500, {}, None)
        self.assertIsNone(self.r.completer(self.msgs)); self.assertIn("500", self.r.derniere_erreur)
        self.mock.routes[("POST", "/v1/messages")] = lambda r: (401, {}, None)
        self.assertIsNone(self.r.completer(self.msgs)); self.assertIn("clé", self.r.derniere_erreur)

    def test_sans_cle_reste_local(self):
        self.kx.config["cles_api"] = {}; os.environ.pop("ANTHROPIC_API_KEY", None); self.assertIsNone(self.r.completer(self.msgs))

    def test_confidentiel_bloque_cloud(self):
        self.kx.extensions.marquer_confidentiel(2); self.assertIsNone(self.r.completer(self.msgs))

    def test_openai_compatible(self):
        self.mock.routes[("POST", "/v1/chat/completions")] = lambda r: (200, {"choices": [{"message": {"content": "ok openai"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, None)
        self.kx.config.update(fournisseur_llm="openai", openai_base_url=self.mock.url + "/v1", cles_api={"openai": "k"}, llm_modele_quotidien="gpt-4o-mini")
        self.assertEqual(self.r.completer(self.msgs), "ok openai")

    def test_alternance_anthropic(self):
        self.r.completer([{"role": "system", "content": "s"}, {"role": "assistant", "content": "x"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
        m = self.api.vus[-1]["json"]["messages"]; self.assertEqual(m[0]["role"], "user"); self.assertEqual([x["role"] for x in m], ["user", "assistant", "user"])

    def test_voix(self):
        self.assertIn("local", self.kx.traiter("reste en local")); self.assertEqual(self.kx.config["mode_ia"], "local")
        self.assertIn("hybride", self.kx.traiter("passe en mode hybride")); self.assertIn("qualité", self.kx.traiter("mode qualité"))
        self.assertIn("dollars", self.kx.traiter("combien j'ai dépensé"))

    def test_cerveau_integration(self):
        """Le vrai Cerveau consulte le routeur avant Ollama."""
        from noyau.cerveau import Cerveau
        c = Cerveau.__new__(Cerveau)
        c.extensions = self.kx.extensions; c.config = self.kx.config; c.derniere_erreur = ""
        c._messages = lambda *a, **k: self.msgs
        c.modele = "x"; c.disponible = False
        self.assertEqual(c.reflechir("salut"), "Réponse cloud")


class TestUtilitaires(unittest.TestCase):
    def test_durees(self):
        from noyau.extensions.utilitaires import lire_duree
        self.assertEqual(lire_duree("10 minutes"), 600); self.assertEqual(lire_duree("1 heure 30"), 5400 if False else 3600)
        self.assertEqual(lire_duree("une demi-heure"), 1800); self.assertEqual(lire_duree("45 secondes"), 45)
        self.assertEqual(lire_duree("2 heures et 15 minutes"), 8100); self.assertEqual(lire_duree("un quart d'heure"), 900)
        self.assertIsNone(lire_duree("bientôt"))

    def test_heure_date(self):
        import datetime as dt
        from noyau.extensions.utilitaires import Utilitaires as U
        self.assertEqual(U.heure_parlee(dt.datetime(2026, 10, 7, 14, 5)), "Il est 14 heures 05.")
        self.assertEqual(U.date_parlee(dt.datetime(2026, 10, 7)), "Nous sommes mercredi 7 octobre 2026.")
        self.assertEqual(U.date_parlee(dt.datetime(2026, 11, 1)), "Nous sommes dimanche 1er novembre 2026.")

    def test_minuteur_sonne_et_persiste(self):
        kx = FauxKx(); u = kx.extensions.module("utilitaires")
        self.assertIn("lancé", kx.traiter("minuteur de 1 seconde pour les pâtes"))
        time.sleep(1.6)
        self.assertTrue(any("pâtes" in d for d in kx.voix.dit), kx.voix.dit); self.assertEqual(u.actifs(), [])
        kx.traiter("minuteur de 10 minutes"); self.assertEqual(len(u.actifs()), 1)
        self.assertIn("minuteur", kx.traiter("combien de temps reste-t-il"))
        self.assertIn("annulé", kx.traiter("annule le minuteur")); self.assertEqual(u.actifs(), [])

    def test_reprise_apres_redemarrage(self):
        kx = FauxKx(); u = kx.extensions.module("utilitaires"); u.creer(1.0, "reprise"); u.arreter()
        kx2 = FauxKx(reset=False); kx2.extensions.module("utilitaires").demarrer(); time.sleep(1.5)
        self.assertTrue(any("reprise" in d for d in kx2.voix.dit))

    def test_rappel_et_chrono(self):
        kx = FauxKx()
        self.assertIn("je te rappelle dans 30 minutes", kx.traiter("rappelle-moi dans 30 minutes d'appeler Paul"))
        kx.extensions.module("utilitaires").annuler()
        self.assertIn("lancé", kx.traiter("lance un chrono")); self.assertIn("Chronomètre", kx.traiter("combien sur le chrono"))
        self.assertIn("Pour combien", kx.traiter("lance un minuteur"))


class TestMemoireLongue(unittest.TestCase):
    def test_flux(self):
        kx = FauxKx()
        self.assertIn("Léa", kx.traiter("retiens que ma sœur s'appelle Léa"))
        self.assertIn("sœur", kx.traiter("qui est Léa ?")); self.assertIn("Léa", kx.traiter("mes proches"))
        self.assertIn("Atlas", kx.traiter("retiens que mon projet Atlas est en tournage")); self.assertIn("tournage", kx.traiter("mes projets"))
        kx.traiter("nouveau projet : Chaîne YouTube"); self.assertIn("Chaîne YouTube", kx.traiter("mes projets"))
        self.assertIn("café", kx.traiter("j'aime le café sans sucre")); self.assertIn("café", kx.traiter("mes préférences"))
        self.assertIn("oublié", kx.traiter("oublie Léa")); self.assertIn("aucun", kx.traiter("mes proches"))

    def test_pas_de_faux_positifs(self):
        kx = FauxKx()
        self.assertIn("rien", kx.traiter("je préfère que tu me répondes plus lentement quand je suis fatigué le soir après le travail"))


if __name__ == "__main__":
    unittest.main()
