; ============================================================
;  KRYPTONIX - installateur Windows (Inno Setup 6)
;  Produit : Kryptonix-Setup.exe  (un seul fichier, ~100-200 Mo)
;
;  Comportement voulu : l'utilisateur double-clique -> tout s'installe
;  sans question -> l'application s'ouvre toute seule. Ollama et le modele
;  d'IA sont installes par l'application elle-meme au premier lancement,
;  avec une barre de progression.
; ============================================================

#define MonNom "KRYPTONIX"
#ifndef MaVersion
  #define MaVersion "1.0.0"
#endif
#define MonEditeur "Kryptonix"

[Setup]
AppId={{9F2C4E7A-5B1D-4C83-A6E0-3D7F1B8C2A54}
AppName={#MonNom}
AppVersion={#MaVersion}
AppPublisher={#MonEditeur}
DefaultDirName={localappdata}\Programs\{#MonNom}
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
DisableFinishedPage=yes
; Installation dans le profil de l'utilisateur : aucun droit administrateur
PrivilegesRequired=lowest
OutputDir=..\sortie
OutputBaseFilename=Kryptonix-Setup
SetupIconFile=..\ressources\kryptonix.ico
UninstallDisplayIcon={app}\KRYPTONIX.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Ferme l'application si elle tourne (mise a jour) et la relance ensuite
CloseApplications=force
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Files]
Source: "..\dist\KRYPTONIX\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MonNom}"; Filename: "{app}\KRYPTONIX.exe"
Name: "{autodesktop}\{#MonNom}"; Filename: "{app}\KRYPTONIX.exe"

[Run]
; Lancement automatique a la fin de l'installation (et apres une mise a jour silencieuse)
Filename: "{app}\KRYPTONIX.exe"; Flags: nowait runasoriginaluser

[UninstallRun]
; Les donnees (memoire, documents) sont dans %APPDATA%\Kryptonix et sont conservees.
