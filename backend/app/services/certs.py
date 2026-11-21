"""Доверенный HTTPS в локальной сети без затрат.

Один раз создаётся собственный корневой сертификат (его устанавливают на телефон), а сертификат
сервера выпускается заново при каждом запуске — на все текущие IP-адреса ноутбука. Смена Wi-Fi
не требует повторной настройки телефона.
"""
from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .. import config
from .netinfo import HOTSPOT_IP, lan_ips

CA_CERT = "dolce-lms-root-ca.crt"
CA_KEY = "dolce-lms-root-ca.key"
SERVER_CERT = "server.crt"
SERVER_KEY = "server.key"


def _write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()))


def ensure_ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    config.CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = config.CERT_DIR / CA_CERT, config.CERT_DIR / CA_KEY
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=30):
            return cert, key
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Dolce Pharm LMS Root CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Dolce Pharm LMS (local demo)"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _write_key(key_path, key)
    return cert, key


def ensure_server_cert() -> tuple[Path, Path, list[str]]:
    """Выпускает сертификат сервера на текущие адреса. Возвращает (cert, key, список имён)."""
    ca_cert, ca_key = ensure_ca()
    ips = ["127.0.0.1", HOTSPOT_IP] + lan_ips()
    ips = list(dict.fromkeys(ips))
    hostname = socket.gethostname()
    dns = ["localhost"]
    if hostname.isascii() and hostname:
        dns += [hostname, hostname + ".local"]
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Dolce Pharm LMS")]))
        .issuer_name(ca_cert.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))      # не дольше 398 дней — требование iOS и Chrome
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                     data_encipherment=False, key_agreement=True, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = config.CERT_DIR / SERVER_CERT, config.CERT_DIR / SERVER_KEY
    # в файл кладём цепочку: сертификат сервера + корневой
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM))
    _write_key(key_path, key)
    return cert_path, key_path, dns + ips
