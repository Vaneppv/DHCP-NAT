#!/usr/bin/env python3
"""Demostracion paso a paso del intercambio DORA (DHCP) con sockets reales.

Este script arranca un servidor DHCP y un cliente DHCP escritos en Python
(ver dhcp_server.py y dhcp_client.py) y muestra, en una sola terminal y en
orden cronologico, los cuatro mensajes del protocolo:

    CLIENTE   ->  DHCPDISCOVER  (broadcast)
    SERVIDOR  ->  DHCPOFFER
    CLIENTE   ->  DHCPREQUEST   (broadcast)
    SERVIDOR  ->  DHCPACK

Modos de ejecucion:

  local (por defecto)  Usa 127.0.0.1 y puertos altos (6767/6868), por lo que
                       NO necesita root ni Docker. Ideal para probar en
                       cualquier computadora.

  red                  Usa los puertos reales 67/68 y broadcast en la red.
                       Pensado para correr dentro de un contenedor Docker:

                           docker compose -f docker-compose.dhcp-py.yml up --build

Ejemplos:

    python3 demo_dora.py
    python3 demo_dora.py --hexdump
    python3 demo_dora.py --modo red --broadcast 192.168.50.255 --configure
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time

import dhcp_client
import dhcp_server

START = time.monotonic()
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
DIM = "\033[2m"

USE_COLOR = True


def style(text: str, *codes: str) -> str:
    """Aplica codigos ANSI solo si los colores estan habilitados."""
    if not USE_COLOR:
        return text
    return "".join(codes) + text + RESET


class DemoFormatter(logging.Formatter):
    """Prefija cada linea con el tiempo transcurrido y quien habla."""

    def __init__(self, use_color: bool = True) -> None:
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        elapsed = time.monotonic() - START
        if record.name == "dhcp.server":
            role, color = "SERVIDOR", MAGENTA
        else:
            role, color = "CLIENTE ", CYAN
        message = record.getMessage()
        if not self.use_color:
            return f"[{elapsed:6.3f}s] {role} | {message}"
        return f"{DIM}[{elapsed:6.3f}s]{RESET} {color}{role}{RESET} | {message}"


def banner(text: str) -> None:
    print()
    print("=" * 74)
    print(" " + style(text, BOLD))
    print("=" * 74)


def note(text: str) -> None:
    print(f"  {text}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Demostracion del intercambio DORA con un cliente y servidor DHCP en Python",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--modo", choices=["local", "red"], default="local",
                        help="local = 127.0.0.1 y puertos altos; red = puertos 67/68 reales")
    parser.add_argument("--range", default="192.168.10.50-192.168.10.80",
                        help="rango de IPs que reparte el servidor")
    parser.add_argument("--router", default="192.168.10.254", help="gateway entregado por DHCP")
    parser.add_argument("--dns", default="8.8.8.8,1.1.1.1", help="servidores DNS entregados")
    parser.add_argument("--mask", default="255.255.255.0", help="mascara de subred entregada")
    parser.add_argument("--lease-time", type=int, default=43200, help="duracion de la concesion (s)")
    parser.add_argument("--interface", default="eth0", help="interfaz de red (modo red)")
    parser.add_argument("--broadcast", default="192.168.50.255",
                        help="broadcast de la red (modo red)")
    parser.add_argument("--configure", action="store_true",
                        help="aplicar la IP recibida a la interfaz (modo red, requiere NET_ADMIN)")
    parser.add_argument("--hexdump", action="store_true", help="mostrar los bytes de cada mensaje")
    parser.add_argument("--hostname", default="pc-python", help="nombre del cliente")
    parser.add_argument("--no-color", action="store_true", help="desactivar colores ANSI")
    args = parser.parse_args(argv)

    use_color = not (args.no_color or os.environ.get("NO_COLOR")) and sys.stdout.isatty()
    global USE_COLOR
    USE_COLOR = use_color

    logging.getLogger().handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(DemoFormatter(use_color))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    if args.modo == "local":
        server_port, client_port = 6767, 6868
        host = "127.0.0.1"
        server_id = "127.0.0.1"
        broadcast = "127.0.0.1"
        interface = ""
        mac = "02:42:ac:11:00:50"
        configure = False
    else:
        server_port, client_port = 67, 68
        host = "0.0.0.0"
        server_id = None  # el servidor detecta la IP de la interfaz
        broadcast = args.broadcast
        interface = args.interface
        mac = None  # se lee la MAC real de la interfaz
        configure = args.configure

    banner("Demo DHCP: intercambio DORA con cliente y servidor propios")
    if args.modo == "local":
        note("Modo local: 127.0.0.1, puertos 6767/6868 (no requiere root)")
        note("El cliente NO modifica la interfaz: solo muestra la configuracion recibida.")
    else:
        note(f"Modo red: interfaz {interface}, puertos reales 67/68")
        note(f"Broadcast: {broadcast}")
    note("")
    note("Los mensajes DORA son:")
    note("  1) DISCOVER  el cliente pregunta si hay algun servidor DHCP")
    note("  2) OFFER     el servidor ofrece una IP libre")
    note("  3) REQUEST   el cliente pide formalmente esa IP")
    note("  4) ACK       el servidor confirma la concesion (lease)")
    print()

    server_argv = [
        "--bind", host,
        "--port", str(server_port),
        "--client-port", str(client_port),
        "--broadcast", broadcast,
        "--range", args.range,
        "--router", args.router,
        "--dns", args.dns,
        "--subnet-mask", args.mask,
        "--lease-time", str(args.lease_time),
    ]
    if server_id:
        server_argv += ["--server-id", server_id]
    if interface:
        server_argv += ["--interface", interface]
    if args.hexdump:
        server_argv.append("--hexdump")

    client_argv = [
        "--bind", host if args.modo == "local" else "0.0.0.0",
        "--port", str(client_port),
        "--server-port", str(server_port),
        "--broadcast", broadcast,
        "--hostname", args.hostname,
        "--timeout", "4",
        "--interface", interface,
    ]
    if mac:
        client_argv += ["--mac", mac]
    if args.hexdump:
        client_argv.append("--hexdump")
    if configure:
        client_argv.append("--configure")

    errors: list[BaseException] = []

    def serve() -> None:
        try:
            dhcp_server.main(server_argv)
        except BaseException as exc:  # se informa en el hilo principal
            errors.append(exc)

    thread = threading.Thread(target=serve, name="dhcp-server", daemon=True)
    thread.start()
    time.sleep(0.5)

    if errors:
        print("No se pudo iniciar el servidor:")
        print(f"  {errors[0]}")
        print("Sugerencia: en modo 'red' los puertos 67/68 requieren root.")
        return 1

    try:
        result = dhcp_client.main(client_argv)
    except Exception as exc:  # noqa: BLE001 - queremos un mensaje claro en clase
        print(f"Error ejecutando el cliente: {exc}")
        result = 1

    print()
    if result == 0:
        print(" " + style("Intercambio DORA completado correctamente", GREEN, BOLD))
    else:
        print(" " + style(f"El intercambio no se completo (codigo {result})", YELLOW, BOLD))

    if args.modo == "red" and configure:
        print()
        print(" Estado de la interfaz despues de aplicar la configuracion DHCP:")
        subprocess.run(["ip", "-brief", "addr", "show", "dev", args.interface], check=False)
        subprocess.run(["ip", "route"], check=False)

    print()
    print(" Que acaba de pasar:")
    print("   * El cliente no tenia IP, por eso DISCOVER y REQUEST van en broadcast.")
    print("   * El servidor identifico al cliente por su MAC (campo chaddr) y por el xid.")
    print("   * La IP queda 'alquilada' durante el lease time; al vencer se libera o renueva.")
    print("   * Ademas de la IP, DHCP entrega mascara, gateway y DNS (opciones 1, 3 y 6).")
    print()
    return 0 if result == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
