#!/usr/bin/env python3
"""Cliente DHCP minimo, escrito solo con la libreria estandar de Python.

Implementa el lado cliente del intercambio DORA:

    1. DHCPDISCOVER  (broadcast)  "hay algun servidor DHCP?"
    2. DHCPOFFER     (respuesta)  "te ofrezco 192.168.10.50"
    3. DHCPREQUEST   (broadcast)  "acepto esa IP"
    4. DHCPACK       (respuesta)  "confirmado, es tuya por N segundos"

Uso tipico dentro de un contenedor (necesita root para el puerto 68):

    python3 dhcp_client.py --interface eth0 --server-port 67 \
        --broadcast 255.255.255.255 --configure

En modo local / de prueba (sin privilegios):

    python3 dhcp_client.py --bind 127.0.0.1 --port 6868 \
        --server-port 6767 --broadcast 127.0.0.1 --mac 02:42:ac:11:00:50
"""

from __future__ import annotations

import argparse
import logging
import random
import socket
import subprocess
import sys
import time

from dhcp_packet import (
    ACK,
    BOOTREQUEST,
    DISCOVER,
    NAK,
    OFFER,
    RELEASE,
    REQUEST,
    DHCPMessage,
    OPT_CLIENT_ID,
    OPT_DNS,
    OPT_HOSTNAME,
    OPT_LEASE_TIME,
    OPT_MAX_SIZE,
    OPT_PARAM_LIST,
    OPT_REQUESTED_IP,
    OPT_ROUTER,
    OPT_SERVER_ID,
    OPT_SUBNET_MASK,
    hexdump,
    mac_to_bytes,
)

LOG = logging.getLogger("dhcp.client")

BROADCAST_FLAG = 0x8000


def interface_mac(interface: str) -> str | None:
    """Lee la MAC real de la interfaz desde /sys (Linux)."""
    try:
        with open(f"/sys/class/net/{interface}/address", encoding="ascii") as handle:
            return handle.read().strip()
    except OSError:
        return None


def random_mac() -> str:
    """Genera una MAC localmente administrada (02:xx:...) para las pruebas."""
    return "02:" + ":".join(f"{random.randrange(256):02x}" for _ in range(5))


def build_discover(mac: str, hostname: str) -> DHCPMessage:
    msg = DHCPMessage(
        op=BOOTREQUEST,
        flags=BROADCAST_FLAG,
        chaddr=mac_to_bytes(mac),
    )
    msg.msg_type = DISCOVER
    # Opcion 61: identificador de cliente (tipo Ethernet + MAC)
    msg.set_bytes(OPT_CLIENT_ID, b"\x01" + mac_to_bytes(mac))
    if hostname:
        msg.set_str(OPT_HOSTNAME, hostname)
    msg.set_u16(OPT_MAX_SIZE, 1500)
    # Opcion 55: que parametros queremos que nos devuelva el servidor
    msg.set_bytes(OPT_PARAM_LIST, bytes([1, 3, 6, 15, 28, 51, 54, 58, 59]))
    return msg


def build_request(mac: str, hostname: str, requested_ip: str, server_id: str, xid: int) -> DHCPMessage:
    msg = DHCPMessage(
        op=BOOTREQUEST,
        xid=xid,
        flags=BROADCAST_FLAG,
        chaddr=mac_to_bytes(mac),
    )
    msg.msg_type = REQUEST
    msg.set_bytes(OPT_CLIENT_ID, b"\x01" + mac_to_bytes(mac))
    if hostname:
        msg.set_str(OPT_HOSTNAME, hostname)
    msg.set_ip(OPT_REQUESTED_IP, requested_ip)
    msg.set_ip(OPT_SERVER_ID, server_id)
    msg.set_u16(OPT_MAX_SIZE, 1500)
    msg.set_bytes(OPT_PARAM_LIST, bytes([1, 3, 6, 15, 28, 51, 54, 58, 59]))
    return msg


def recv_until(
    sock: socket.socket,
    xid: int,
    wanted: set[int],
    timeout: float,
) -> tuple[DHCPMessage, tuple[str, int]] | None:
    """Espera un mensaje con el xid indicado y alguno de los tipos esperados."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        sock.settimeout(remaining)
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            return None
        try:
            msg = DHCPMessage.from_bytes(data)
        except ValueError as exc:
            LOG.debug("Paquete ignorado desde %s: %s", addr, exc)
            continue
        if msg.xid != xid:
            LOG.debug("xid distinto (0x%08x), ignorado", msg.xid)
            continue
        if msg.msg_type not in wanted:
            LOG.debug("tipo %s inesperado, ignorado", msg.type_name)
            continue
        LOG.info("Recibidos %d bytes desde %s:%d", len(data), addr[0], addr[1])
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug("Bytes recibidos:\n%s", hexdump(data))
        return msg, addr


def apply_configuration(interface: str, ip: str, mask: str, router: str | None) -> None:
    """Aplica la configuracion recibida a la interfaz (requiere CAP_NET_ADMIN)."""
    if not interface:
        LOG.info("Sin --interface: no se modifica la interfaz (solo demostracion)")
        return
    commands = [["ip", "addr", "replace", f"{ip}/{mask}", "dev", interface]]
    if router:
        commands.append(["ip", "route", "replace", "default", "via", router, "dev", interface])
    for command in commands:
        LOG.info("Ejecutando: %s", " ".join(command))
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            LOG.warning("Fallo '%s': %s", " ".join(command), result.stderr.strip())


def run(args: argparse.Namespace) -> int:
    mac = args.mac or (interface_mac(args.interface) if args.interface else None) or random_mac()
    LOG.info("Cliente DHCP iniciando")
    LOG.info("  MAC        : %s", mac)
    LOG.info("  Interfaz   : %s", args.interface or "(ninguna)")
    LOG.info("  Socket     : %s:%d", args.bind, args.port)
    LOG.info("  Servidor   : %s:%d", args.broadcast, args.server_port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((args.bind, args.port))

    try:
        # ------------------------------------------------------------------
        # 1. DISCOVER
        # ------------------------------------------------------------------
        offered = None
        discover = None
        for attempt in range(1, args.retries + 1):
            discover = build_discover(mac, args.hostname)
            LOG.info("PASO 1/4  Enviando DHCPDISCOVER (broadcast, xid=0x%08x)", discover.xid)
            LOG.info("          aun no tengo IP: origen %s, destino %s:%d",
                     discover.ciaddr, args.broadcast, args.server_port)
            if args.hexdump:
                LOG.info("Bytes enviados:\n%s", hexdump(discover.to_bytes()))
            sock.sendto(discover.to_bytes(), (args.broadcast, args.server_port))

            result = recv_until(sock, discover.xid, {OFFER}, args.timeout)
            if result:
                offered, _ = result
                break
            LOG.warning("Intento %d/%d sin respuesta; reintentando...", attempt, args.retries)

        if offered is None:
            LOG.error("No se recibio ningun DHCPOFFER. Revisar servidor, rango y firewall.")
            return 1

        server_id = offered.get_ip(OPT_SERVER_ID) or ""
        ip = offered.yiaddr
        LOG.info("PASO 2/4  DHCPOFFER recibido: el servidor %s ofrece %s", server_id, ip)
        if args.hexdump:
            LOG.info("%s", offered.pretty())

        # ------------------------------------------------------------------
        # 2. REQUEST
        # ------------------------------------------------------------------
        request = build_request(mac, args.hostname, ip, server_id, discover.xid)
        LOG.info("PASO 3/4  Enviando DHCPREQUEST: acepto %s del servidor %s", ip, server_id)
        sock.sendto(request.to_bytes(), (args.broadcast, args.server_port))

        result = recv_until(sock, request.xid, {ACK, NAK}, args.timeout)
        if result is None:
            LOG.error("No se recibio respuesta del servidor al DHCPREQUEST")
            return 1
        reply, _ = result
        if reply.msg_type == NAK:
            LOG.error("El servidor respondio DHCPNAK: la IP %s no es valida", ip)
            return 1
        ack = reply
        if args.hexdump:
            LOG.info("%s", ack.pretty())

        # ------------------------------------------------------------------
        # 3. Configuracion obtenida
        # ------------------------------------------------------------------
        mask = ack.get_ip(OPT_SUBNET_MASK) or "255.255.255.0"
        routers = ack.get_ips(OPT_ROUTER)
        dns = ack.get_ips(OPT_DNS)
        lease_time = ack.get_u32(OPT_LEASE_TIME)

        LOG.info("PASO 4/4  DHCPACK recibido: concesion confirmada")
        LOG.info("          IP       : %s", ack.yiaddr)
        LOG.info("          Mascara  : %s", mask)
        LOG.info("          Gateway  : %s", ", ".join(routers) or "(sin gateway)")
        LOG.info("          DNS      : %s", ", ".join(dns) or "(sin DNS)")
        LOG.info("          Duracion : %s s", lease_time if lease_time is not None else "?")
        LOG.info("          Servidor : %s", server_id)

        if args.configure:
            apply_configuration(args.interface, ack.yiaddr, mask, routers[0] if routers else None)
        else:
            LOG.info("(modo demostracion: no se modifica la interfaz; usar --configure para aplicarla)")

        if args.release:
            release = DHCPMessage(op=BOOTREQUEST, flags=BROADCAST_FLAG, chaddr=mac_to_bytes(mac))
            release.msg_type = RELEASE
            release.ciaddr = ack.yiaddr
            release.set_ip(OPT_SERVER_ID, server_id)
            release.set_bytes(OPT_CLIENT_ID, b"\x01" + mac_to_bytes(mac))
            sock.sendto(release.to_bytes(), (server_id or args.broadcast, args.server_port))
            LOG.info("DHCPRELEASE enviado: liberamos %s", ack.yiaddr)
        return 0
    finally:
        sock.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cliente DHCP minimo en Python (implementacion didactica del RFC 2131)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--bind", default="0.0.0.0", help="direccion local del cliente")
    parser.add_argument("--port", type=int, default=68, help="puerto UDP local del cliente")
    parser.add_argument("--server-port", type=int, default=67, help="puerto UDP del servidor")
    parser.add_argument("--broadcast", default="255.255.255.255", help="direccion destino de los mensajes")
    parser.add_argument("--interface", default="eth0", help="interfaz de la que se toma la MAC")
    parser.add_argument("--mac", default=None, help="MAC a usar (si no se indica, se lee de la interfaz)")
    parser.add_argument("--hostname", default="pc-python", help="nombre del host (opcion 12)")
    parser.add_argument("--timeout", type=float, default=4.0, help="espera maxima por respuesta (s)")
    parser.add_argument("--retries", type=int, default=3, help="cantidad de DISCOVER a enviar")
    parser.add_argument("--configure", action="store_true", help="aplicar IP/ruta a la interfaz con 'ip'")
    parser.add_argument("--release", action="store_true", help="enviar DHCPRELEASE al final")
    parser.add_argument("--hexdump", action="store_true", help="mostrar los bytes de cada mensaje")
    return parser


def setup_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    try:
        return run(args)
    except PermissionError as exc:
        LOG.error("Permiso denegado (%s). Los puertos 67/68 requieren root o CAP_NET_BIND_SERVICE.", exc)
        return 2
    except KeyboardInterrupt:
        LOG.info("Cancelado por el usuario")
        return 130


if __name__ == "__main__":
    sys.exit(main())
