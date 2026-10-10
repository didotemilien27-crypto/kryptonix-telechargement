"""
KRYPTONIX / noyau/extensions/ws_mini.py
========================================
Client WebSocket minimal (RFC 6455) en bibliothèque standard : aucune dépendance à installer.
Suffisant pour OBS WebSocket (texte JSON). Les fonctions d'encodage/décodage de trames sont
publiques pour permettre un serveur de test.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class ErreurWS(Exception):
    pass


def cle_acceptation(cle: str) -> str:
    return base64.b64encode(hashlib.sha1((cle + GUID).encode()).digest()).decode()


def encoder_trame(payload: bytes, opcode: int = 1, masque: bool = True) -> bytes:
    tete = bytes([0x80 | opcode])
    n = len(payload)
    bit = 0x80 if masque else 0
    if n < 126:
        tete += bytes([bit | n])
    elif n < 65536:
        tete += bytes([bit | 126]) + struct.pack(">H", n)
    else:
        tete += bytes([bit | 127]) + struct.pack(">Q", n)
    if masque:
        m = os.urandom(4)
        return tete + m + bytes(b ^ m[i % 4] for i, b in enumerate(payload))
    return tete + payload


def _lire(sock: socket.socket, n: int) -> bytes:
    tampon = b""
    while len(tampon) < n:
        morceau = sock.recv(n - len(tampon))
        if not morceau:
            raise ErreurWS("connexion fermée")
        tampon += morceau
    return tampon


def lire_trame(sock: socket.socket) -> tuple[int, bool, bytes]:
    """→ (opcode, fin, charge utile démasquée)"""
    a, b = _lire(sock, 2)
    fin, opcode = bool(a & 0x80), a & 0x0F
    masque, n = bool(b & 0x80), b & 0x7F
    if n == 126:
        n = struct.unpack(">H", _lire(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _lire(sock, 8))[0]
    m = _lire(sock, 4) if masque else b""
    charge = _lire(sock, n) if n else b""
    if masque:
        charge = bytes(c ^ m[i % 4] for i, c in enumerate(charge))
    return opcode, fin, charge


class _SocketAvecTampon:
    """Socket qui rend d'abord les octets déjà lus par erreur (ex. le message d'accueil d'OBS arrivé
    dans le même paquet TCP que la réponse de poignée de main)."""

    def __init__(self, sock: socket.socket, deja_lu: bytes = b""):
        self._sock, self._tampon = sock, deja_lu

    def recv(self, n: int) -> bytes:
        if self._tampon:
            donnees, self._tampon = self._tampon[:n], self._tampon[n:]
            return donnees
        return self._sock.recv(n)

    def __getattr__(self, nom):
        return getattr(self._sock, nom)


class ClientWS:
    def __init__(self, hote: str, port: int, chemin: str = "/", sous_protocoles: str = "", timeout: float = 6.0):
        self.hote, self.port, self.chemin = hote, port, chemin
        self.sous_protocoles, self.timeout = sous_protocoles, timeout
        self.sock: socket.socket | None = None

    def connecter(self) -> None:
        try:
            self.sock = socket.create_connection((self.hote, self.port), timeout=self.timeout)
        except OSError as e:
            raise ErreurWS(f"connexion impossible ({e.__class__.__name__})") from None
        cle = base64.b64encode(os.urandom(16)).decode()
        requete = (f"GET {self.chemin} HTTP/1.1\r\nHost: {self.hote}:{self.port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {cle}\r\nSec-WebSocket-Version: 13\r\n")
        if self.sous_protocoles:
            requete += f"Sec-WebSocket-Protocol: {self.sous_protocoles}\r\n"
        self.sock.sendall((requete + "\r\n").encode())
        reponse = b""
        while b"\r\n\r\n" not in reponse:
            morceau = self.sock.recv(1024)
            if not morceau:
                raise ErreurWS("poignée de main refusée")
            reponse += morceau
            if len(reponse) > 8192:
                raise ErreurWS("réponse trop longue")
        brut_entete, _, reste = reponse.partition(b"\r\n\r\n")
        self.sock = _SocketAvecTampon(self.sock, reste)
        entete = brut_entete.decode("latin-1")
        if " 101 " not in entete.split("\r\n")[0]:
            raise ErreurWS("le serveur n'a pas accepté la mise à niveau WebSocket")
        if cle_acceptation(cle).lower() not in entete.lower():
            raise ErreurWS("clé d'acceptation invalide")

    def envoyer(self, texte: str) -> None:
        if not self.sock:
            raise ErreurWS("non connecté")
        try:
            self.sock.sendall(encoder_trame(texte.encode("utf-8"), 1, True))
        except OSError:
            raise ErreurWS("connexion perdue") from None

    def recevoir(self, timeout: float | None = None) -> str:
        if not self.sock:
            raise ErreurWS("non connecté")
        if timeout is not None:
            self.sock.settimeout(timeout)
        morceaux = b""
        while True:
            try:
                opcode, fin, charge = lire_trame(self.sock)
            except socket.timeout:
                raise ErreurWS("délai dépassé") from None
            except OSError:
                raise ErreurWS("connexion perdue") from None
            if opcode == 9:                                   # ping → pong
                self.sock.sendall(encoder_trame(charge, 10, True))
                continue
            if opcode == 8:
                raise ErreurWS("connexion fermée par le serveur")
            if opcode in (0, 1, 2):
                morceaux += charge
                if fin:
                    return morceaux.decode("utf-8", errors="replace")

    def fermer(self) -> None:
        if self.sock:
            try:
                self.sock.sendall(encoder_trame(b"", 8, True))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
