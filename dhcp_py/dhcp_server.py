#!/usr/bin/env python3
"""Servidor DHCP minimo, escrito solo con la libreria estandar de Python.

Implementa el lado servidor del intercambio DORA:

    recibe DHCPDISCOVER -> responde DHCPOFFER
    recibe DHCPREQUEST  -> responde DHCPACK (o DHCPNAK)
    recibe DHCPRELEASE  -> libera la concesion
    recibe DHCPDECLINE  -> descarta la IP ofrecida
    recibe DHCPINFORM   -> responde configuracion sin asignar IP

Se puede ejecutar de dos maneras:

1) En una red real (necesita root para usar el puerto 67), por ejemplo dentro
   de un contenedor Docker:

       sudo python3 dhcp_server.py \
           --interface eth0 \
           --range 192.168.10.50-192.168.10.80 \
           --router 192.168.10.254 \
           --dns 8.8.8.8,1.1.1.1 \
           --server-id 192.168.10.2

2) En modo de prueba local, sin privilegios (puertos altos en 127.0.0.1).
   Normalmente esto lo maneja demo_dora.py.
"""

from __future__ import annotations

import argparse
import logging
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass

from dhcp_packet import (
    ACK,
    BOOTREPLY,
    DECLINE,
    DISCOVER,
    INFORM,
    NAK,
    OFFER,
    RELEASE,
    REQUEST,
    DHCPMessage,
    OPT_BROADCAST,
    OPT_DNS,
    OPT_DOMAIN,
    OPT_HOSTNAME,
    OPT_LEASE_TIME,
    OPT_ROUTER,
    OPT_SERVER_ID,
    OPT_SUBNET_MASK,
    OPT_T1,
    OPT_T2,
    hexdump,
    int_to_ip,
    ip_to_int,
)

LOG = logging.getLogger("dhcp.server")


# ---------------------------------------------------------------------------
# Concesiones
# ---------------------------------------------------------------------------

@dataclass
class Lease:
    mac: str
    ip: str
    hostname: str
    expiry: float          # timestamp absoluto (time.time())
    client_id: str = ""
    pending: bool = False  # True mientras es solo una OFERTA sin confirmar

    @property
    def active(self) -> bool:
        return self.expiry > time.time()


# ---------------------------------------------------------------------------
# Servidor
# ---------------------------------------------------------------------------

class DHCPServer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.pool = self._parse_range(args.range)
        self.server_id = args.server_id or self._detect_ipv4(args.interface)
        # mac -> Lease
        self.leases: dict[str, Lease] = {}
        # IP reservada para una MAC concreta (--reserve mac=ip)
        self.reserved_by_ip, self.reserved_by_mac = self._parse_reservations(args.reserve)
        self.sock: socket.socket | None = None

    # -- configuracion -------------------------------------------------------

    @staticmethod
    def _parse_range(text: str) -> list[str]:
        try:
            first, last = text.split("-")
            start, end = ip_to_int(first.strip()), ip_to_int(last.strip())
        except ValueError as exc:
            raise SystemExit(f"Rango invalido '{text}'. Formato esperado: 192.168.10.50-192.168.10.80") from exc
        if end < start:
            raise SystemExit("El rango DHCP termina antes de empezar")
        return [int_to_ip(value) for value in range(start, end + 1)]

    @staticmethod
    def _parse_reservations(items: list[str]) -> tuple[dict[str, str], dict[str, str]]:
        by_ip: dict[str, str] = {}
        by_mac: dict[str, str] = {}
        for item in items or []:
            try:
                mac, ip = item.split("=")
            except ValueError as exc:
                raise SystemExit(f"Reserva invalida '{item}'. Formato esperado: mac=ip") from exc
            mac = mac.strip().lower()
            ip = ip.strip()
            by_ip[ip] = mac
            by_mac[mac] = ip
        return by_ip, by_mac

    @staticmethod
    def _detect_ipv4(interface: str | None) -> str:
        """Obtiene la IP de la interfaz (la que ira en la opcion 54)."""
        if interface:
            try:
                out = subprocess.run(
                    ["ip", "-4", "-o", "addr", "show", "dev", interface],
                    capture_output=True, text=True, timeout=3, check=False,
                )
                for token in out.stdout.split():
                    if "/" in token and token[0].isdigit():
                        return token.split("/")[0]
            except (OSError, subprocess.SubprocessError):
                pass
        # Alternativa: preguntarle al kernel que IP usaria para salir a Internet
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 53))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            probe.close()

    # -- socket --------------------------------------------------------------

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if self.args.interface:
            # Enviar/recibir solo por esa interfaz (equivale a --interface de dnsmasq)
            try:
                sock.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_BINDTODEVICE,
                    (self.args.interface + "\x00").encode(),
                )
            except (AttributeError, PermissionError, OSError):
                LOG.warning(
                    "No se pudo fijar la interfaz %s (falta CAP_NET_RAW); se usa la ruta por defecto",
                    self.args.interface,
                )
        sock.bind((self.args.bind, self.args.port))
        sock.settimeout(1.0)
        return sock

    # -- arranque ------------------------------------------------------------

    def serve_forever(self) -> None:
        self.sock = self._open_socket()
        LOG.info("=" * 68)
        LOG.info("Servidor DHCP en escucha en %s:%d", self.args.bind, self.args.port)
        LOG.info("Interfaz      : %s", self.args.interface or "(cualquiera)")
        LOG.info("Server ID (54): %s", self.server_id)
        LOG.info("Rango         : %s - %s (%d direcciones)", self.pool[0], self.pool[-1], len(self.pool))
        LOG.info("Gateway (3)   : %s", ", ".join(self.args.router))
        LOG.info("DNS (6)       : %s", ", ".join(self.args.dns))
        LOG.info("Lease (51)    : %d s (%d h)", self.args.lease_time, self.args.lease_time // 3600)
        if self.reserved_by_mac:
            for mac, ip in self.reserved_by_mac.items():
                LOG.info("Reserva       : %s -> %s", mac, ip)
        LOG.info("=" * 68)

        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                self._expire()
                continue
            except OSError as exc:
                LOG.error("Error de socket: %s", exc)
                continue
            try:
                msg = DHCPMessage.from_bytes(data)
            except ValueError as exc:
                LOG.warning("Paquete descartado desde %s: %s", addr, exc)
                continue
            if msg.op != 1:  # BOOTREQUEST
                continue
            try:
                self._handle(msg, addr)
            except Exception as exc:  # nunca tumbar el servidor por un paquete raro
                LOG.exception("Error procesando %s: %s", msg.type_name, exc)

    # -- logica DHCP ---------------------------------------------------------

    def _handle(self, msg: DHCPMessage, addr: tuple[str, int]) -> None:
        kind = msg.msg_type
        if kind == DISCOVER:
            self._on_discover(msg)
        elif kind == REQUEST:
            self._on_request(msg)
        elif kind == RELEASE:
            self._on_release(msg)
        elif kind == DECLINE:
            self._on_decline(msg)
        elif kind == INFORM:
            self._on_inform(msg)
        else:
            LOG.warning("Mensaje %s no manejado (desde %s)", msg.type_name, addr)

    # DISCOVER -------------------------------------------------------------

    def _on_discover(self, msg: DHCPMessage) -> None:
        mac = msg.mac
        LOG.info("DISCOVER  recibido de %s (xid=0x%08x)", mac, msg.xid)

        requested = msg.get_ip(50)
        ip = self._choose_ip(mac, requested)
        if ip is None:
            LOG.warning("No quedan direcciones libres en el rango; DISCOVER ignorado")
            return

        # Oferta valida por 60 s mientras el cliente decide
        self.leases[mac] = Lease(
            mac=mac,
            ip=ip,
            hostname=msg.get_str(OPT_HOSTNAME) or "*",
            expiry=time.time() + 60,
            client_id=(msg.options.get(61) or b"").hex(),
            pending=True,
        )
        if requested:
            LOG.info("           el cliente pidio %s; se ofrece %s", requested, ip)
        LOG.info("PASO 2/4  Enviando DHCPOFFER: te ofrezco %s", ip)

        reply = self._new_reply(msg)
        reply.yiaddr = ip
        reply.msg_type = OFFER
        self._add_common_options(reply, pending=True)
        self._send(reply)

    # REQUEST --------------------------------------------------------------

    def _on_request(self, msg: DHCPMessage) -> None:
        mac = msg.mac
        server_id = msg.get_ip(OPT_SERVER_ID)
        requested = msg.get_ip(50) or msg.ciaddr
        LOG.info(
            "REQUEST   recibido de %s (xid=0x%08x) pide %s al servidor %s",
            mac, msg.xid, requested or "?", server_id or "(no indicado)",
        )

        if server_id and server_id != self.server_id:
            LOG.info("           el cliente eligio otro servidor (%s): ignoramos", server_id)
            return

        lease = self.leases.get(mac)
        if not requested:
            self._nak(msg, mac, "el REQUEST no indica IP")
            return
        if lease and lease.ip != requested:
            LOG.info("           el cliente tenia oferta %s y pide %s", lease.ip, requested)
        if requested not in self.pool and self.reserved_by_mac.get(mac) != requested:
            self._nak(msg, mac, f"{requested} no pertenece al rango DHCP")
            return
        reserved_to = self.reserved_by_ip.get(requested)
        if reserved_to and reserved_to != mac:
            self._nak(msg, mac, f"{requested} esta reservada para {reserved_to}")
            return
        if not self._is_free(requested, mac):
            self._nak(msg, mac, f"{requested} ya esta en uso por otro cliente")
            return

        hostname = msg.get_str(OPT_HOSTNAME) or (lease.hostname if lease else "*")
        self.leases[mac] = Lease(
            mac=mac,
            ip=requested,
            hostname=hostname,
            expiry=time.time() + self.args.lease_time,
            client_id=(msg.options.get(61) or b"").hex(),
        )
        LOG.info(
            "PASO 4/4  Enviando DHCPACK: %s queda asignada a %s por %d s",
            requested, mac, self.args.lease_time,
        )
        self._write_lease_file()
        self._print_leases()

        reply = self._new_reply(msg)
        reply.yiaddr = requested
        reply.msg_type = ACK
        self._add_common_options(reply)
        self._send(reply)

    # RELEASE / DECLINE / INFORM -------------------------------------------

    def _on_release(self, msg: DHCPMessage) -> None:
        mac = msg.mac
        lease = self.leases.pop(mac, None)
        if lease:
            LOG.info("RELEASE   %s libera %s", mac, lease.ip)
            self._write_lease_file()
        else:
            LOG.info("RELEASE   de %s sin concesion activa", mac)

    def _on_decline(self, msg: DHCPMessage) -> None:
        mac = msg.mac
        lease = self.leases.pop(mac, None)
        LOG.warning(
            "DECLINE   %s rechaza %s (posible conflicto de IP)",
            mac, lease.ip if lease else "?",
        )

    def _on_inform(self, msg: DHCPMessage) -> None:
        LOG.info("INFORM    de %s: solo pedimos configuracion, no IP", msg.mac)
        reply = self._new_reply(msg)
        reply.msg_type = ACK
        self._add_common_options(reply)
        self._send(reply)

    # -- helpers -------------------------------------------------------------

    def _choose_ip(self, mac: str, requested: str | None) -> str | None:
        """Elige la IP a ofrecer respetando reservas y concesiones vigentes."""
        # 1. Reserva explicita para esta MAC
        reserved = self.reserved_by_mac.get(mac)
        if reserved:
            if self._is_free(reserved, mac):
                return reserved
            LOG.warning("La IP reservada %s para %s esta ocupada", reserved, mac)

        # 2. Concesion vigente (renovacion)
        current = self.leases.get(mac)
        if current and current.active:
            return current.ip

        # 3. La IP que el cliente pide, si esta libre y no esta reservada a otro
        if requested and requested in self.pool and self._is_free(requested, mac):
            return requested

        # 4. Primera IP libre del rango
        for ip in self.pool:
            if self._is_free(ip, mac):
                return ip
        return None

    def _is_free(self, ip: str, mac: str) -> bool:
        now = time.time()
        for other_mac, lease in self.leases.items():
            if other_mac != mac and lease.ip == ip and lease.expiry > now:
                return False
        reserved_to = self.reserved_by_ip.get(ip)
        if reserved_to and reserved_to != mac:
            return False
        return True

    def _expire(self) -> None:
        now = time.time()
        expired = [mac for mac, lease in self.leases.items() if lease.expiry <= now]
        for mac in expired:
            lease = self.leases.pop(mac)
            if not lease.pending:
                LOG.info("Concesion vencida: %s (%s) liberada", lease.ip, mac)
        if expired:
            self._write_lease_file()

    def _new_reply(self, request: DHCPMessage) -> DHCPMessage:
        return DHCPMessage(
            op=BOOTREPLY,
            xid=request.xid,
            flags=request.flags,
            ciaddr=request.ciaddr,
            siaddr=self.server_id,
            chaddr=request.chaddr,
        )

    def _add_common_options(self, reply: DHCPMessage, pending: bool = False) -> None:
        reply.set_ip(OPT_SERVER_ID, self.server_id)
        reply.set_u32(OPT_LEASE_TIME, 60 if pending else self.args.lease_time)
        reply.set_ip(OPT_SUBNET_MASK, self.args.subnet_mask)
        reply.set_ips(OPT_ROUTER, self.args.router)
        reply.set_ips(OPT_DNS, self.args.dns)
        if self.args.domain:
            reply.set_str(OPT_DOMAIN, self.args.domain)
        if not pending:
            reply.set_u32(OPT_T1, self.args.lease_time // 2)
            reply.set_u32(OPT_T2, int(self.args.lease_time * 0.875))
        reply.set_ip(OPT_BROADCAST, self.args.broadcast_address)

    def _nak(self, request: DHCPMessage, mac: str, reason: str) -> None:
        LOG.warning("PASO 4/4  Enviando DHCPNAK a %s: %s", mac, reason)
        reply = self._new_reply(request)
        reply.msg_type = NAK
        reply.set_ip(OPT_SERVER_ID, self.server_id)
        self._send(reply)

    def _send(self, reply: DHCPMessage) -> None:
        assert self.sock is not None
        # Si el cliente no tiene IP (ciaddr 0.0.0.0) o pidio broadcast,
        # la respuesta debe ir a 255.255.255.255 para que la reciba.
        if reply.broadcast_flag or reply.ciaddr == "0.0.0.0":
            destination = (self.args.broadcast, self.args.client_port)
        else:
            destination = (reply.yiaddr, self.args.client_port)
        data = reply.to_bytes()
        self.sock.sendto(data, destination)
        LOG.debug("Enviados %d bytes a %s", len(data), destination)
        if self.args.hexdump:
            LOG.info("Bytes enviados (%d):\n%s", len(data), hexdump(data))

    def _print_leases(self) -> None:
        active = [lease for lease in self.leases.values() if lease.active and not lease.pending]
        if not active:
            return
        LOG.info("Concesiones activas:")
        LOG.info("  %-18s %-16s %-10s %s", "MAC", "IP", "Restante", "Host")
        for lease in sorted(active, key=lambda item: ip_to_int(item.ip)):
            remaining = max(0, int(lease.expiry - time.time()))
            if remaining >= 3600:
                human = f"{remaining // 3600}h{(remaining % 3600) // 60:02d}m"
            else:
                human = f"{remaining // 60}m{remaining % 60:02d}s"
            LOG.info(
                "  %-18s %-16s %-10s %s",
                lease.mac, lease.ip, human, lease.hostname,
            )

    def _write_lease_file(self) -> None:
        if not self.args.lease_file:
            return
        try:
            with open(self.args.lease_file, "w", encoding="utf-8") as handle:
                for lease in self.leases.values():
                    if lease.active and not lease.pending:
                        handle.write(
                            f"{int(lease.expiry)} {lease.mac} {lease.ip} "
                            f"{lease.hostname} {lease.client_id}\n"
                        )
        except OSError as exc:
            LOG.warning("No se pudo escribir %s: %s", self.args.lease_file, exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Servidor DHCP minimo en Python (implementacion didactica del RFC 2131)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--bind", default="0.0.0.0", help="direccion local donde escuchar")
    parser.add_argument("--port", type=int, default=67, help="puerto UDP del servidor DHCP")
    parser.add_argument("--client-port", type=int, default=68, help="puerto UDP del cliente")
    parser.add_argument("--interface", default=None, help="interfaz de red a usar (ej. eth0)")
    parser.add_argument("--range", default="192.168.10.50-192.168.10.80", help="rango de IPs a repartir")
    parser.add_argument("--server-id", default=None, help="IP que identifica al servidor (opcion 54)")
    parser.add_argument("--subnet-mask", default="255.255.255.0", help="mascara entregada (opcion 1)")
    parser.add_argument("--router", default="192.168.10.254", help="gateway(s) entregados (opcion 3)")
    parser.add_argument("--dns", default="8.8.8.8,1.1.1.1", help="DNS entregados (opcion 6)")
    parser.add_argument("--domain", default="oficina.local", help="dominio entregado (opcion 15)")
    parser.add_argument("--broadcast-address", default="192.168.10.255", help="broadcast de la subred")
    parser.add_argument("--lease-time", type=int, default=43200, help="duracion de la concesion en segundos")
    parser.add_argument("--broadcast", default="255.255.255.255", help="direccion de broadcast para responder")
    parser.add_argument("--reserve", action="append", default=[], metavar="MAC=IP", help="reserva fija (repetible)")
    parser.add_argument("--lease-file", default=None, help="archivo donde guardar las concesiones")
    parser.add_argument("--hexdump", action="store_true", help="mostrar los bytes de cada mensaje enviado")
    return parser


def normalize(args: argparse.Namespace) -> argparse.Namespace:
    """Convierte listas separadas por comas en listas de Python."""
    args.router = [item.strip() for item in args.router.split(",") if item.strip()]
    args.dns = [item.strip() for item in args.dns.split(",") if item.strip()]
    return args


def setup_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )


def main(argv: list[str] | None = None) -> int:
    args = normalize(build_parser().parse_args(argv))
    setup_logging()
    server = DHCPServer(args)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Servidor detenido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
