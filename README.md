MAC Monitor
Un personnage animé dans la barre de menu macOS qui surveille ton système en temps réel.

Aperçu
L'icône dans la barre de menu change de couleur selon l'état de ton CPU :

Couleur	État CPU
🟢 Vert	< 35% — au repos
🔵 Bleu	35–60% — occupé
🟠 Orange	60–80% — chaud
🔴 Rouge	> 80% — surchargé
Le texte à côté affiche en cycle : CPU% → RAM% → Réseau → Horloge (clic pour changer).

Disponible en 5 icônes : robot · ghost · alien · ninja · cat (changeable depuis l'onglet Process).

Installation
git clone https://github.com/owen008-in/mac-monitor.git
cd mac-monitor
bash install.sh

Le script gère tout automatiquement :

Installation de Homebrew si absent
Installation de Python 3.9+
Installation des dépendances pip
Création d'un LaunchAgent pour le démarrage automatique
Création de MAC Monitor.app dans /Applications
Fonctionnalités
Onglet Système
CPU — usage global + sparkline 60s + barre + fréquence + température
GPU — usage GPU (si disponible)
RAM — usage + label pression (Normal / Élevé / Critique) avec couleur synchronisée
Batterie — pourcentage + barre + temps restant + santé (%)
Fuseaux horaires — UTC · New York · Tokyo en bas du panel
Onglet Réseau
Vitesse temps réel — téléchargement ↓ et upload ↑, mis à jour chaque seconde
Sparklines — historique 60s des vitesses
WiFi — SSID + barres de signal + ping vers 8.8.8.8
Totaux — données téléchargées/envoyées depuis le démarrage
VPN — détection automatique (WireGuard, Tailscale, OpenVPN…)
Stockage — usage disque + vitesses lecture/écriture en temps réel
Onglet Utils
Calendrier — 5 prochains événements sur 5 jours (Google Calendar ou Apple Calendar)
Musique — titre et artiste en cours (Apple Music ou Spotify) + boutons ◀◀ ⏯ ▶▶
Météo — conditions actuelles via wttr.in (géolocalisation automatique par IP)
Onglet Process
Top 10 processus — triés par CPU ou RAM (clic sur l'en-tête pour basculer)
Boutons d'action :
Bouton	Fonction
💤 Veille	Active/désactive l'anti-veille (caffeinate)
⏰ Pomo	Timer Pomodoro 25 minutes
📋 Copier	Snapshot complet des stats dans le presse-papiers
🔒 Verrou	Verrouille l'écran
Icônes	Choisir le personnage (robot/ghost/alien/ninja/cat)
Notifications automatiques
Déclencheur	Seuil
CPU surchargé	> 90%
RAM saturée	> 90%
Batterie faible	≤ 10% sans charge
Température CPU	> 90°C
Téléchargement terminé	Vitesse repasse sous 100 KB/s après un pic
Réunion imminente	15 min avant, puis 5 min avant
Consommation ressources
État	CPU typique
Panel fermé	~2–4%
Panel ouvert	~8–12%
Le panel fermé suspend les redraws et ralentit l'animation à ~1 fps.

Google Calendar (optionnel)
Par défaut l'onglet Utils utilise Apple Calendar. Pour connecter Google Calendar :

Installer gcalcli (fait automatiquement par install.sh si pipx est dispo)
Lancer la config :
gcalcli --config-folder ~/.config/gcalcli init

Entrer ton client_id et client_secret (Google Cloud Console → API Calendar → Credentials OAuth Desktop)
S'authentifier dans le navigateur
Gestion du démarrage automatique
# Vérifier l'état
launchctl list com.macmonitor.app

# Arrêter
launchctl unload ~/Library/LaunchAgents/com.macmonitor.app.plist

# Redémarrer
launchctl load -w ~/Library/LaunchAgents/com.macmonitor.app.plist

# Logs
tail -f /tmp/macmonitor.log
tail -f /tmp/macmonitor.err

L'app MAC Monitor.app dans /Applications sert aussi de bouton on/off.

Dépendances
Package	Rôle
rumps	Framework menubar macOS
psutil	Métriques système (CPU, RAM, réseau, disque)
pyobjc-core	Bridge Python → Objective-C
pyobjc-framework-Cocoa	APIs macOS (NSView, NSColor, NSTimer…)
gcalcli	Google Calendar (optionnel, installé via pipx)
Configuration requise
macOS 12 Monterey ou supérieur
Apple Silicon ou Intel
Python 3.9+ (installé automatiquement si absent)

Fichiers
mac-monitor/
├── menubar_monitor.py   — code principal
├── install.sh           — installation complète
├── requirements.txt     — dépendances Python
└── README.md

Licence
MIT — libre d'utilisation, modification et distribution.

