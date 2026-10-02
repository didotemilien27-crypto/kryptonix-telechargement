"""
KRYPTONIX / interfaces/hud.py
==============================
HUD flottant : barre minimaliste, semi-transparente, toujours au premier plan,
déplaçable à la souris, avec saisie texte, bouton micro, télémétrie compacte
et réponse affichée en direct.

Contrainte technique respectée : Tkinter n'est pas thread-safe. Le noyau
diffuse ses événements depuis des threads de fond ; ils sont donc empilés
dans une file et consommés par `after()` dans le thread graphique.
"""

from __future__ import annotations

import logging
import queue
import threading
import webbrowser

import customtkinter as ctk

LOG = logging.getLogger("hud")

ctk.set_appearance_mode("Dark")

# Palette alignée sur le dashboard
FOND = "#06080d"
PANNEAU = "#0e141d"
ARETE = "#1b2735"
CRISTAL = "#8fd9ff"
GIVRE = "#e6f1f8"
SOURD = "#5d6e7e"
SIGNAL = "#ff4d4d"
AMBRE = "#ffc266"

LIBELLES_ETAT = {
    "veille": "veille",
    "ecoute": "écoute",
    "reflexion": "réflexion",
    "parole": "parole",
    "ingestion": "ingestion",
}


class HUD(ctk.CTk):
    """Fenêtre flottante compacte pilotant le noyau."""

    def __init__(self, kryptonix):
        super().__init__()
        self.kryptonix = kryptonix
        self.config_hud = kryptonix.config
        self._evenements: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._deplie = False

        self._construire_fenetre()
        self._construire_widgets()
        self._positionner()

        kryptonix.abonner(self._recevoir)
        self.after(120, self._consommer_evenements)
        intervalle_telemetrie = 4000 if kryptonix.config.get("mode_leger") else 1500
        self.after(intervalle_telemetrie, self._rafraichir_telemetrie)

    # ------------------------------------------------------------------
    #  Fenêtre
    # ------------------------------------------------------------------
    def _construire_fenetre(self) -> None:
        self.title("KRYPTONIX")
        self.overrideredirect(True)            # ni bordure ni barre de titre
        self.attributes("-topmost", True)      # toujours au-dessus
        try:
            self.attributes("-alpha", float(self.config_hud.get("hud_opacite", 0.92)))
        except Exception:
            pass
        self.configure(fg_color=FOND)
        self.protocol("WM_DELETE_WINDOW", self.fermer)
        self.bind("<Escape>", lambda _e: self.basculer_repli())

    def _positionner(self) -> None:
        largeur, hauteur = 620, 108
        ecran_l = self.winfo_screenwidth()
        ecran_h = self.winfo_screenheight()
        marge = 28
        coins = {
            "bas_droite": (ecran_l - largeur - marge, ecran_h - hauteur - 70),
            "bas_gauche": (marge, ecran_h - hauteur - 70),
            "haut_droite": (ecran_l - largeur - marge, marge),
            "haut_gauche": (marge, marge),
        }
        x, y = coins.get(self.config_hud.get("hud_position", "bas_droite"),
                         coins["bas_droite"])
        self.geometry(f"{largeur}x{hauteur}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------------
    #  Widgets
    # ------------------------------------------------------------------
    def _construire_widgets(self) -> None:
        cadre = ctk.CTkFrame(self, fg_color=PANNEAU, corner_radius=10,
                             border_width=1, border_color=ARETE)
        cadre.pack(fill="both", expand=True, padx=6, pady=6)

        # --- Ligne 1 : poignée, état, télémétrie, actions ---
        haut = ctk.CTkFrame(cadre, fg_color="transparent")
        haut.pack(fill="x", padx=12, pady=(9, 3))

        self.poignee = ctk.CTkLabel(haut, text="KRYPTONIX", font=("Chakra Petch", 13, "bold"),
                                    text_color=GIVRE)
        self.poignee.pack(side="left")
        for widget in (haut, self.poignee, cadre):
            widget.bind("<Button-1>", self._prendre)
            widget.bind("<B1-Motion>", self._glisser)

        self.pastille = ctk.CTkLabel(haut, text="●", font=("Arial", 13), text_color=SOURD)
        self.pastille.pack(side="left", padx=(10, 4))
        self.etiquette_etat = ctk.CTkLabel(haut, text="veille", font=("Consolas", 11),
                                           text_color=SOURD)
        self.etiquette_etat.pack(side="left")

        self.telemetrie = ctk.CTkLabel(haut, text="—", font=("Consolas", 11), text_color=SOURD)
        self.telemetrie.pack(side="left", padx=16)

        ctk.CTkButton(haut, text="✕", width=26, height=24, font=("Arial", 12),
                      fg_color="transparent", hover_color=SIGNAL, text_color=SOURD,
                      command=self.fermer).pack(side="right")
        ctk.CTkButton(haut, text="◧", width=26, height=24, font=("Arial", 12),
                      fg_color="transparent", hover_color=ARETE, text_color=SOURD,
                      command=self.ouvrir_dashboard).pack(side="right", padx=2)
        self.bouton_micro = ctk.CTkButton(
            haut, text="MICRO", width=64, height=24, font=("Consolas", 10),
            fg_color="transparent", border_width=1, border_color=ARETE,
            hover_color=ARETE, text_color=SOURD, command=self.basculer_micro)
        self.bouton_micro.pack(side="right", padx=6)

        # --- Ligne 2 : saisie ---
        bas = ctk.CTkFrame(cadre, fg_color="transparent")
        bas.pack(fill="x", padx=12, pady=(0, 4))

        self.champ = ctk.CTkEntry(
            bas, placeholder_text="ouvre youtube · télémétrie · retiens que... · aspire C:\\Ultron",
            font=("Consolas", 12), height=32, fg_color=FOND, border_color=ARETE,
            text_color=GIVRE, placeholder_text_color="#3f4d5c")
        self.champ.pack(side="left", fill="x", expand=True)
        self.champ.bind("<Return>", self._envoyer)
        self.champ.focus()

        ctk.CTkButton(bas, text="▶", width=38, height=32, font=("Arial", 12),
                      fg_color="transparent", border_width=1, border_color=CRISTAL,
                      hover_color=ARETE, text_color=CRISTAL,
                      command=self._envoyer).pack(side="left", padx=(8, 0))

        # --- Ligne 3 : dernière réponse ---
        self.reponse = ctk.CTkLabel(cadre, text="Prêt.", font=("Consolas", 11),
                                    text_color=CRISTAL, anchor="w", justify="left",
                                    wraplength=572)
        self.reponse.pack(fill="x", padx=12, pady=(0, 9))

    # ------------------------------------------------------------------
    #  Déplacement à la souris
    # ------------------------------------------------------------------
    def _prendre(self, evenement) -> None:
        self._origine = (evenement.x_root - self.winfo_x(), evenement.y_root - self.winfo_y())

    def _glisser(self, evenement) -> None:
        if not hasattr(self, "_origine"):
            return
        self.geometry(f"+{evenement.x_root - self._origine[0]}+{evenement.y_root - self._origine[1]}")

    # ------------------------------------------------------------------
    #  Actions
    # ------------------------------------------------------------------
    def _envoyer(self, _evenement=None) -> None:
        texte = self.champ.get().strip()
        if not texte:
            return
        self.champ.delete(0, "end")
        self._afficher("Traitement...", SOURD)
        # Le traitement part dans un thread : l'interface ne gèle jamais.
        threading.Thread(
            target=self.kryptonix.traiter, args=(texte,),
            kwargs={"source": "hud"}, daemon=True,
        ).start()

    def basculer_micro(self) -> None:
        actif = self.kryptonix.basculer_ecoute()
        if not self.kryptonix.voix.micro_disponible:
            self._afficher("Aucun micro détecté sur cette machine.", AMBRE)
            return
        self.bouton_micro.configure(
            text_color=CRISTAL if actif else SOURD,
            border_color=CRISTAL if actif else ARETE,
        )

    def ouvrir_dashboard(self) -> None:
        hote = self.kryptonix.config.get("web_hote", "127.0.0.1")
        port = self.kryptonix.config.get("web_port", 5000)
        webbrowser.open(f"http://{hote}:{port}")

    def basculer_repli(self) -> None:
        """Escape : réduit le HUD à sa barre d'état, ou le redéploie."""
        self._deplie = not self._deplie
        self.geometry("620x40" if self._deplie else "620x108")

    def fermer(self) -> None:
        try:
            self.kryptonix.arreter()
        finally:
            self.destroy()

    # ------------------------------------------------------------------
    #  Pont thread-safe noyau → interface
    # ------------------------------------------------------------------
    def _recevoir(self, evenement: str, donnees: dict) -> None:
        """Appelé depuis n'importe quel thread : on empile seulement."""
        self._evenements.put((evenement, donnees))

    def _consommer_evenements(self) -> None:
        while True:
            try:
                evenement, donnees = self._evenements.get_nowait()
            except queue.Empty:
                break
            if evenement == "etat":
                self._maj_etat(donnees.get("etat", "veille"))
            elif evenement == "message" and donnees.get("role") == "assistant":
                self._afficher(donnees.get("contenu", ""), CRISTAL)
            elif evenement == "message" and donnees.get("source") == "voix":
                self._afficher("« " + donnees.get("contenu", "") + " »", SOURD)
            elif evenement == "alerte":
                self._afficher(donnees.get("message", ""), SIGNAL)
        self.after(120, self._consommer_evenements)

    def _maj_etat(self, etat: str) -> None:
        couleurs = {"veille": SOURD, "ecoute": CRISTAL, "reflexion": AMBRE,
                    "parole": CRISTAL, "ingestion": AMBRE}
        self.pastille.configure(text_color=couleurs.get(etat, SOURD))
        self.etiquette_etat.configure(text=LIBELLES_ETAT.get(etat, etat),
                                      text_color=couleurs.get(etat, SOURD))

    def _afficher(self, texte: str, couleur: str = CRISTAL) -> None:
        if len(texte) > 320:
            texte = texte[:317] + "..."
        self.reponse.configure(text=texte, text_color=couleur)

    def _rafraichir_telemetrie(self) -> None:
        mesure = self.kryptonix.systeme.telemetrie
        if mesure:
            couleur = SIGNAL if max(mesure.get("cpu", 0), mesure.get("ram", 0)) > 90 else SOURD
            self.telemetrie.configure(
                text=f"cpu {mesure.get('cpu', 0):.0f}%  ram {mesure.get('ram', 0):.0f}%",
                text_color=couleur,
            )
        self.after(2000 if not self.config_hud.get("mode_leger") else 5000,
                  self._rafraichir_telemetrie)


def lancer_hud(kryptonix) -> None:
    """Ouvre le HUD. Doit impérativement tourner dans le thread principal."""
    fenetre = HUD(kryptonix)
    fenetre.mainloop()
