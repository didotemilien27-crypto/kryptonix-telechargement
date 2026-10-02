# KRYPTONIX — le guide ultime

Tout ce qu'il faut savoir, du premier lancement jusqu'à la publication en ligne. Un seul document, dans l'ordre, pour ne jamais
avoir à chercher ailleurs.

---

## Sommaire

1. [Ce qu'est KRYPTONIX, honnêtement](#1-ce-quest-kryptonix-honnêtement)
2. [Installation développeur](#2-installation-complète-pas-à-pas)
3. [Premier lancement](#3-premier-lancement)
4. [Toutes les commandes, par fonctionnalité](#4-toutes-les-commandes-par-fonctionnalité)
5. [Le mode léger — PC portable, machine modeste](#5-le-mode-léger--pc-portable-machine-modeste)
6. [Les deux interfaces](#6-les-deux-interfaces)
7. [L'application installable et sa publication](#7-lapplication-installable-et-sa-publication)
8. [Où sont mes données ?](#8-où-sont-mes-données-)
9. [Réglages avancés — config.json](#9-réglages-avancés--configjson)
10. [Pannes courantes](#10-pannes-courantes)

---

## 1. Ce qu'est KRYPTONIX, honnêtement

KRYPTONIX est un assistant personnel qui tourne **entièrement sur ta machine**.
Pas de compte, pas d'abonnement, pas de serveur externe qui lit tes
conversations. En échange de cette liberté, il faut accepter deux réalités :

- **Il a besoin de puissance locale.** Le modèle de langage (le "cerveau")
  tourne sur ton processeur ou ta carte graphique. Une machine modeste doit
  utiliser un petit modèle (voir la [section 5](#5-le-mode-léger--pc-portable-machine-modeste)).
- **Il ne peut pas être "essayé en ligne".** Contrairement à un chatbot
  hébergé, il n'existe pas de version web où l'IA tourne déjà — il faut
  l'installer une fois, chez toi.

Ce que KRYPTONIX sait réellement faire :

| Fonction | Réalité |
|---|---|
| Conversation, mémoire, profil | Complet, local, persistant |
| Rédaction de documents (Word, PDF, Excel, CSV, HTML) | Complet |
| Voix de sortie | Neuronale (edge-tts) si internet, sinon hors ligne (pyttsx3) |
| Anticipation des besoins | Heuristique par mots-clés, pas de la magie |
| Analyse et nettoyage du PC | Complet, jamais tes documents personnels |
| Tuteur scolaire (NSI, français, SES, histoire, langues...) | Un cadrage du modèle, pas une intelligence supérieure |
| Lecture de texte dans une image (OCR) | Complet, nécessite Tesseract installé |
| Description visuelle d'une image | Nécessite un modèle multimodal (`ollama pull llava`) |
| Génération de sons | Tonalités procédurales, pas de la musique composée |
| Génération de vidéos | Diaporamas texte, pas de vidéo animée par IA |

---

## 2. Installation complète, pas à pas

### 2.1 — Python

1. Va sur **python.org/downloads**, télécharge la version 3.12
2. Lance l'installeur, **coche "Add python.exe to PATH"**
3. Vérifie dans une invite de commande : `python --version`

### 2.2 — Ollama (le cerveau)

1. Va sur **ollama.com/download**, télécharge et installe (aucun droit
   administrateur requis, ça s'installe dans ton profil personnel)
2. Télécharge un modèle :
   ```
   ollama pull mistral
   ```
   (machine puissante, 8 Go de RAM conseillés) ou, pour une machine modeste :
   ```
   ollama pull gemma:2b
   ```
3. Pour la vraie description d'images (optionnel) :
   ```
   ollama pull llava
   ```

### 2.3 — Le projet KRYPTONIX

1. Décompresse `KRYPTONIX.zip` dans `C:\KRYPTONIX`
2. Ouvre une invite de commande dedans :
   ```
   cd C:\KRYPTONIX
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Vérifie que tout est en place :
   ```
   python lancer.py --diagnostic
   ```

### 2.4 — Extras optionnels

- **OCR (lecture de texte dans les images)** : installe Tesseract depuis
  `github.com/UB-Mannheim/tesseract/wiki`, puis `pip install pytesseract`
- **PyAudio (micro)** échoue souvent à l'installation : ce n'est pas grave,
  KRYPTONIX fonctionne sans micro. En cas de besoin :
  `pip install pipwin && pipwin install pyaudio`

---

## 3. Premier lancement

```
python lancer.py
```

Ce qui doit se passer : une barre flottante (le HUD) apparaît, le navigateur
s'ouvre sur `http://127.0.0.1:5000`, et une voix annonce "Kryptonix en ligne."

Les fois suivantes, double-clique simplement sur **`lancer.bat`**.

---

## 4. Toutes les commandes, par fonctionnalité

### Conversation et mémoire

| Tu dis | KRYPTONIX fait |
|---|---|
| `retiens que mon lycée est ...` | Enregistre le fait dans ton profil |
| `que sais-tu sur moi` | Restitue tout ce qu'il sait |
| `oublie tout` | Efface l'historique (garde le profil et les documents) |

### Web et navigation

| Tu dis | KRYPTONIX fait |
|---|---|
| `ouvre youtube` / `ouvre github` | Ouvre le site, avec le lien affiché |
| `cherche recette de crêpes` | Lance une recherche Google |

### Rédaction de documents

```
crée un [type] [sujet] en [format]
```
- **Types** : rapport, document, texte, lettre de motivation, lettre, cv, fiche,
  présentation, tableau, checklist — ou directement un format (`crée un word ...`)
- **Formats** : word, pdf, excel, csv, html, markdown, texte

Exemples :
- `crée un rapport sur le harcèlement scolaire en word`
- `crée une fiche sur la photosynthèse en pdf`
- `crée un tableau de mes dépenses en excel`

Chaque document généré revient avec un **lien de téléchargement direct**.

### PC et matériel

| Tu dis | KRYPTONIX fait |
|---|---|
| `télémétrie` / `diagnostic` | CPU, RAM, disque, batterie en direct |
| `quels sont les composants de mon pc` | Nom exact du processeur, carte graphique, disques |
| `analyse mon pc` | Cherche fichiers temporaires et gros fichiers oubliés |
| `nettoie les fichiers temporaires` | Supprime en sécurité (jamais tes documents) |
| `liste les processus gourmands` | Les 5 plus voraces en RAM |

### Images

```
analyse cette image : C:\chemin\vers\photo.jpg
```
Lit le texte visible (OCR) et décrit le contenu si `llava` est installé.

### Sons et vidéos

| Tu dis | KRYPTONIX fait |
|---|---|
| `génère un son de notification` | Fichier .wav (tonalité procédurale) |
| `génère une vidéo sur [sujet]` | Diaporama .mp4 (texte sur fond coloré) |

### Code

```
exécute ce code python : print(2**10)
```
La sandbox du dashboard (onglet **SANDBOX**) prend en charge sept langages :
Python, JavaScript (Node.js), Bash, C, C++, Java et Perl — un menu déroulant
choisit le langage. Chacun a ses propres garde-fous adaptés (imports système,
réseau et écriture disque refusés automatiquement) ; un langage dont
l'interpréteur ou le compilateur n'est pas installé sur la machine est
signalé clairement plutôt que de planter.

### Matières scolaires

Aucune commande spéciale : mentionne juste la matière ou le contexte
("en NSI", "pour mon bac de français", "en espagnol"...) et le ton, le
vocabulaire et la structure de la réponse s'adaptent tout seuls. Le jeu
vidéo est traité comme une matière à part entière (genres, plateformes,
studios, esport) : demande-lui par exemple "c'est quoi un roguelike" ou
"quels jeux a fait FromSoftware".

### Météo et actualités en direct

| Tu dis | KRYPTONIX fait |
|---|---|
| `météo` | Donne le temps qu'il fait dans la ville de `ville_meteo` (config.json) |
| `météo à Lyon` | Donne le temps pour une ville précisée à l'oral |
| `actualités` / `quoi de neuf dans le monde` | Résume les derniers titres des flux RSS configurés |

Aucune clé API : la météo vient d'Open-Meteo (géocodage + prévisions, gratuit,
sans compte) et les actualités de flux RSS publics (`flux_rss` dans
`config.json`). Le dashboard web affiche la météo en direct dans la colonne
de gauche (rafraîchie toutes les `meteo_intervalle` secondes) et un bandeau
défilant d'actualités sous l'en-tête (rafraîchi toutes les
`actualites_intervalle` secondes).

### Étudier un document précis

Dans le dashboard, chaque document ingéré propose désormais **télécharger**
(récupère le fichier original — PDF compris) et **étudier ce document**, qui
ouvre un petit champ de question posée uniquement sur ce document (et non
sur toute la bibliothèque). La réponse s'appuie sur le contenu intégral du
fichier plutôt que sur une recherche par mot-clé — utile pour un contrat,
un cours ou un rapport que tu veux vraiment interroger en détail.

### Le petit jeu pendant la réflexion

Tant que KRYPTONIX réfléchit (texte ou voix), un mini-jeu d'arcade
vertical façon "sauteur néon" apparaît sous le journal de dialogue : un
personnage rebondit automatiquement de plateforme en plateforme, tu le
diriges juste à gauche/droite (flèches, `A`/`D`, ou tapotement de chaque
côté de l'écran sur mobile). Le jeu se ferme tout seul dès que la réponse
arrive ; le bouton **masquer** le cache manuellement à tout moment.

### Une voix plus réaliste

Avant d'être lu à voix haute, le texte est nettoyé de tout ce qui se lit à
l'écran mais ne se dit pas à l'oral : dièses, astérisques, puces de liste,
liens, blocs de code, emojis... Les nombres et symboles sont aussi
convertis en mots ("12°C" devient "12 degrés", "6.9 Go" devient "6 virgule
9 gigaoctets"). La réponse est ensuite synthétisée phrase par phrase : la
première phrase part à l'oral pendant que les suivantes se préparent, pour
une diction plus fluide et un démarrage plus rapide.

Le moteur vocal essaie, dans l'ordre : **edge-tts** (voix neuronale
Microsoft, la plus naturelle, gratuite, nécessite internet), puis **Piper**
si tu l'as installé (voix neuronale 100 % hors ligne, voir `config.json`),
puis la voix du système via **pyttsx3** (plus robotique mais toujours
disponible). En cas de coupure réseau en pleine phrase, KRYPTONIX bascule
automatiquement sur le moteur suivant sans rester muet.

### Personnaliser l'interface

Dans le dashboard, l'onglet **RÉGLAGES** permet de choisir :
- la **couleur** de l'interface (cristal, ambre, magenta, émeraude, violet, monochrome) ;
- la **forme** des éléments (angles nets ou arrondis) ;
- la **taille** de l'interface (compacte, normale, grande) ;
- la **voix** (plusieurs voix françaises et accents francophones), le
  **moteur vocal**, sa **vitesse** et sa **hauteur**.

Chaque changement est appliqué immédiatement et enregistré dans
`config.json` : il est donc conservé au prochain lancement, sur cette
machine.

---

## 5. Le mode léger — PC portable, machine modeste

`mistral` (7 milliards de paramètres) demande ~8 Go de RAM. Sur une petite
machine, c'est presque toujours la cause d'un blocage — pas le reste du code.

```
python lancer.py --leger --modele gemma:2b --console
```

Ou, en un double-clic : **`lancer_leger.bat`**.

Ce que ça change automatiquement : petit modèle, contexte de conversation
réduit, télémétrie moins fréquente, HUD désactivé, voix hors ligne uniquement.

---

## 6. Les deux interfaces

**HUD flottant** — barre discrète toujours au premier plan. Glisse-la en
attrapant son titre, `Échap` la replie, `✕` éteint tout proprement.

**Dashboard web** (`http://127.0.0.1:5000`) — terminal de dialogue, sandbox de
code, dépôt de documents par glisser-déposer avec étude ciblée par document,
bouton **Analyser mon PC**, télémétrie en direct, météo en direct, bandeau
d'actualités défilant, et un petit jeu d'arcade pendant que le cerveau réfléchit.

---

## 7. L'application installable et sa publication

KRYPTONIX est maintenant une vraie application Windows (`Kryptonix-Setup.exe`) :
double-clic, installation sans question, l'IA s'ouvre. Ollama et le modèle sont
installés automatiquement au premier lancement.

**Tout est expliqué dans `MARCHE_A_SUIVRE.md`** : publication sur GitHub, page de
téléchargement, mises à jour. Pour développer, `lancer.bat` / `python lancer.py`
fonctionnent toujours comme avant.

---

## 8. Où sont mes données ?

Application installée : `%APPDATA%\Kryptonix` (mémoire, documents, réglages,
logs). Elles survivent aux mises à jour et à la désinstallation.

---

## 9. Réglages avancés — config.json

| Clé | Rôle |
|---|---|
| `nom_utilisateur` | Comment il s'adresse à toi |
| `mot_reveil` | Mot déclencheur du micro |
| `ollama_modele` | Modèle utilisé |
| `temperature` | 0.3 = factuel, 0.9 = créatif |
| `voix_style` | `vivienne`, `remy`, `denise`, `henri`, `eloise`, `sylvie_canada`, `jean_canada`, `charline_belgique`, `ariane_suisse` |
| `moteur_tts` | `auto`, `edge`, `piper`, `pyttsx3`, `gtts`, `muet` |
| `hauteur_voix` | Hauteur de la voix, de `-30` (grave) à `30` (aigu) — edge-tts |
| `piper_modele` / `piper_executable` | Chemin du modèle et de l'exécutable Piper (voix hors ligne, optionnelle) |
| `apparence_palette` | `cristal`, `ambre`, `magenta`, `emeraude`, `violet`, `mono` |
| `apparence_forme` | `nette` ou `douce` |
| `apparence_taille` | `compacte`, `normale`, `grande` |
| `mode_leger` | Économie de ressources activée en permanence |
| `hud_position` | `bas_droite`, `bas_gauche`, `haut_droite`, `haut_gauche` |
| `sandbox_active` | Passe à `false` pour interdire toute exécution de code |
| `ville_meteo` | Ville utilisée par la météo en direct |
| `meteo_intervalle` | Secondes entre deux rafraîchissements météo (défaut 900) |
| `actualites_intervalle` | Secondes entre deux rafraîchissements des actualités (défaut 1800) |
| `flux_rss` | Liste des flux RSS lus pour le fil d'actualités |

---

## 10. Pannes courantes

| Symptôme | Cause et remède |
|---|---|
| "Mon cortex local est hors ligne" | `ollama serve` n'est pas lancé |
| Réponses très lentes | Modèle trop lourd → mode léger + `gemma:2b` |
| Le micro n'apparaît pas | PyAudio absent, ou pas de micro détecté |
| "crée un [type]" ne génère rien | Vérifie la formule exacte : section 4 |
| Description d'image générique/absente | `ollama pull llava` non fait |
| OCR ne lit rien | Tesseract non installé sur le système |
| Port 5000 occupé | `python lancer.py --port 8080` |
| Le HUD ne s'ouvre pas | `pip install customtkinter`, ou `--sans-hud` |
| "service météo injoignable" / fil d'actualités vide | Pas de connexion internet, ou site source temporairement en panne |

Le journal complet est dans `logs/kryptonix.log`.
