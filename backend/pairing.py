"""Pareamento de celulares por código de 6 dígitos e anúncio na rede (mDNS).

Fluxo: o app Android acha o DiakoPad pelo serviço `_diakopad._tcp` e pede
para parear (POST /api/pair/request). O servidor gera um código e o mostra
SÓ nas telas já autorizadas (a tela do laptop); quem pediu nunca recebe o
código. O celular digita o código (POST /api/pair/confirm) e recebe um token
próprio, que pode ser revogado no Config sem trocar o DIAKOPAD_TOKEN.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

CODE_TTL_S = 120
MAX_ATTEMPTS = 5
MAX_PENDING = 3
MAX_REQUESTS_PER_MINUTE = 10
DEVICE_NAME_MAX = 60
SERVICE_TYPE = "_diakopad._tcp"


class PairingError(Exception):
    """Erro mostrado ao celular; `status` vira o código HTTP."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class PendingRequest:
    request_id: str
    device_name: str
    code: str
    client_host: Optional[str]
    expires_at: float
    attempts: int = 0
    via_qr: bool = False

    def public(self) -> dict:
        """Formato enviado às telas autorizadas (inclui o código)."""
        return {
            "request_id": self.request_id,
            "device_name": self.device_name,
            "code": self.code,
            "expires_at": self.expires_at,
            "via_qr": self.via_qr,
        }


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clean_device_name(name: str) -> str:
    cleaned = " ".join("".join(ch for ch in str(name) if ch.isprintable()).split())
    return cleaned[:DEVICE_NAME_MAX] or "Celular"


@dataclass
class PairingManager:
    requests: dict[str, PendingRequest] = field(default_factory=dict)
    recent: list[float] = field(default_factory=list)

    def _expire(self, now: float) -> bool:
        expired = [rid for rid, r in self.requests.items() if r.expires_at <= now]
        for rid in expired:
            del self.requests[rid]
        return bool(expired)

    def pending(self, now: Optional[float] = None) -> list[dict]:
        self._expire(time.time() if now is None else now)
        return [r.public() for r in sorted(self.requests.values(), key=lambda r: r.expires_at)]

    def create(
        self, device_name: str, client_host: Optional[str], now: Optional[float] = None, via_qr: bool = False
    ) -> PendingRequest:
        now = time.time() if now is None else now
        self._expire(now)
        self.recent = [t for t in self.recent if now - t < 60]
        if len(self.recent) >= MAX_REQUESTS_PER_MINUTE:
            raise PairingError(429, "Muitos pedidos de pareamento. Espere um minuto.")
        # Um novo pedido do mesmo aparelho substitui o anterior.
        for rid, r in list(self.requests.items()):
            if client_host is not None and r.client_host == client_host:
                del self.requests[rid]
        if len(self.requests) >= MAX_PENDING:
            raise PairingError(429, "Já há pedidos esperando no laptop. Recuse ou aguarde.")
        self.recent.append(now)
        request = PendingRequest(
            request_id=secrets.token_urlsafe(12),
            device_name=clean_device_name(device_name),
            code=f"{secrets.randbelow(1_000_000):06d}",
            client_host=client_host,
            expires_at=now + CODE_TTL_S,
            via_qr=via_qr,
        )
        self.requests[request.request_id] = request
        return request

    def confirm(self, request_id: str, code: str, now: Optional[float] = None) -> PendingRequest:
        """Valida o código. Devolve o pedido (já removido) se estiver certo."""
        now = time.time() if now is None else now
        self._expire(now)
        request = self.requests.get(request_id)
        if request is None:
            raise PairingError(410, "O pedido expirou ou foi recusado. Peça um novo código.")
        if secrets.compare_digest(request.code, str(code).strip()):
            del self.requests[request_id]
            return request
        request.attempts += 1
        if request.attempts >= MAX_ATTEMPTS:
            del self.requests[request_id]
            raise PairingError(410, "Código errado vezes demais. Peça um novo código.")
        restantes = MAX_ATTEMPTS - request.attempts
        raise PairingError(403, f"Código incorreto. Restam {restantes} tentativas.")

    def reject(self, request_id: str) -> bool:
        return self.requests.pop(request_id, None) is not None


def server_name() -> str:
    return os.environ.get("DIAKOPAD_NAME") or f"DiakoPad ({socket.gethostname()})"


def _cli_option(name: str) -> Optional[str]:
    """Lê `--name valor` ou `--name=valor` da linha de comando do uvicorn."""
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def listen_host() -> str:
    # Há serviços que passam --host direto ao uvicorn, sem DIAKOPAD_HOST.
    return os.environ.get("DIAKOPAD_HOST") or _cli_option("--host") or "127.0.0.1"


def listen_port() -> str:
    return os.environ.get("DIAKOPAD_PORT") or _cli_option("--port") or "8080"


def lan_address() -> str:
    """IP:porta que o celular deve usar (vai no QR). DIAKOPAD_PUBLIC_ADDRESS
    força um valor; senão usa o IP da interface da rota padrão (o connect em
    UDP não envia nada)."""
    forced = os.environ.get("DIAKOPAD_PUBLIC_ADDRESS")
    if forced:
        return forced
    ip = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            ip = sock.getsockname()[0]
    except OSError:
        pass
    if not ip or ip.startswith("127."):
        ip = f"{socket.gethostname()}.local"
    return f"{ip}:{listen_port()}"


def pair_link(address: str, request: PendingRequest) -> str:
    """Link lido pelo app (QR ou câmera do celular): diakopad://pair?..."""
    query = urlencode({"a": address, "r": request.request_id, "c": request.code, "n": server_name()})
    return f"diakopad://pair?{query}"


def qr_svg(text: str) -> str:
    try:
        import segno
    except ImportError as e:  # dependência nova; o deploy por git não roda pip
        raise PairingError(503, "QR indisponível: instale as dependências (pip install -r requirements.lock)") from e
    return segno.make(text, error="m").svg_inline(scale=6, border=2, dark="#111111", light="#ffffff")


class Advertiser:
    """Anuncia `_diakopad._tcp` via avahi-publish-service enquanto o servidor
    roda. Só anuncia quando escuta na rede (DIAKOPAD_HOST fora do loopback):
    anunciar um servidor preso ao localhost só confundiria o celular."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None

    def start(self, version: str) -> None:
        host = listen_host()
        if host in {"127.0.0.1", "::1", "localhost"}:
            logger.info("mDNS: servidor só no localhost, sem anúncio na rede")
            return
        binary = shutil.which("avahi-publish-service")
        if binary is None:
            logger.warning("mDNS: avahi-publish-service ausente (instale avahi-utils); o app não vai achar o DiakoPad sozinho")
            return
        port = listen_port()
        try:
            self._proc = subprocess.Popen(
                [binary, server_name(), SERVICE_TYPE, port, f"version={version}", "pair=1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("mDNS: anunciando %s em %s:%s", SERVICE_TYPE, host, port)
        except OSError:
            logger.exception("mDNS: falha ao iniciar o anúncio")

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
