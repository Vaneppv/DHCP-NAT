#!/usr/bin/env python3
"""Demo interactiva del laboratorio DHCP + NAT sobre Docker.

Este script no implementa la red: la levanta y ejecuta, en orden y con
explicaciones, los comandos que muestran el funcionamiento de DHCP y NAT.
Es la herramienta para la defensa en clase.

Uso:

    python3 demo_redes.py            # menu interactivo
    python3 demo_redes.py dora       # ejecuta una accion concreta
    python3 demo_redes.py all        # recorrido completo de la demo

Acciones:
    up        Levantar el laboratorio
    status    Ver estado e IPs de cada contenedor
    dhcp      Ver IP, ruta y concesiones del cliente DHCP
    dora      Capturar el intercambio DHCP completo (DORA) con tcpdump
    nat       Ver las reglas NAT y sus contadores
    traffic   Generar trafico interno -> externo y capturarlo
    conntrack Ver la tabla de conexiones NAT del router
    dnat      Acceder a un servidor interno desde la red externa (port forwarding)
    ping      Probar conectividad interna, externa y a Internet
    down      Apagar y limpiar el laboratorio

Requisitos: Docker en marcha. En Linux, si el usuario no esta en el grupo
'docker', el script reintenta con sudo (puede pedir la contrasena).
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Presentacion
# ---------------------------------------------------------------------------

NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()
BOLD = "" if NO_COLOR else "\033[1m"
DIM = "" if NO_COLOR else "\033[2m"
RESET = "" if NO_COLOR else "\033[0m"
GREEN = "" if NO_COLOR else "\033[32m"
YELLOW = "" if NO_COLOR else "\033[33m"
CYAN = "" if NO_COLOR else "\033[36m"
RED = "" if NO_COLOR else "\033[31m"


def title(text: str) -> None:
    print()
    print("=" * 76)
    print(f" {BOLD}{text}{RESET}")
    print("=" * 76)


def step(number: str, text: str) -> None:
    print(f"\n{BOLD}{CYAN}[{number}]{RESET} {text}")


def explain(text: str) -> None:
    for line in text.strip().splitlines():
        print(f"  {line}")


def ok(text: str) -> None:
    print(f"  {GREEN}OK{RESET} {text}")


def warn(text: str) -> None:
    print(f"  {YELLOW}!{RESET} {text}")


def fail(text: str) -> None:
    print(f"  {RED}x{RESET} {text}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Acceso a Docker
# ---------------------------------------------------------------------------

class DockerError(RuntimeError):
    pass


class Lab:
    """Encapsula como llamar a docker / docker compose segun el sistema."""

    def __init__(self) -> None:
        self.docker = self._detect_docker()
        if self.docker == ["sudo"]:
            # Pedir la contrasena una sola vez, al inicio, y validar el acceso.
            result = subprocess.run(
                self.docker + ["docker", "info"], stdout=subprocess.DEVNULL, check=False
            )
            if result.returncode != 0:
                raise DockerError(
                    "No se pudo acceder a Docker con sudo. Revisa que el servicio "
                    "este corriendo o agrega tu usuario al grupo docker."
                )
        self.compose = self._detect_compose()

    # -- deteccion -----------------------------------------------------------

    @staticmethod
    def _works(cmd: list[str]) -> bool:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
        return result.returncode == 0

    @classmethod
    def _detect_docker(cls) -> list[str]:
        if shutil.which("docker") is None:
            raise DockerError(
                "No se encontro el comando 'docker'. Instala Docker Desktop "
                "(Windows/macOS) o Docker Engine (Linux)."
            )
        if cls._works(["docker", "info"]):
            return []
        if os.name != "nt" and shutil.which("sudo"):
            if cls._works(["sudo", "-n", "docker", "info"]):
                return ["sudo"]
            warn("Docker requiere permisos de administrador: se usara 'sudo'.")
            warn("Puede pedirte la contrasena (queda guardada unos minutos).")
            return ["sudo"]
        raise DockerError(
            "Docker no responde. Abre Docker Desktop o inicia el servicio "
            "(sudo systemctl start docker). En Linux tambien podes agregar tu "
            "usuario al grupo docker: sudo usermod -aG docker $USER"
            " (y volver a iniciar sesion)."
        )

    def _detect_compose(self) -> list[str]:
        # 'docker compose version' es local: no necesita acceder al daemon.
        if self._works(["docker", "compose", "version"]):
            return ["compose"]
        if shutil.which("docker-compose") and self._works(["docker-compose", "version"]):
            return ["docker-compose"]
        raise DockerError("No se encontro 'docker compose' ni 'docker-compose'.")

    # -- ejecucion -----------------------------------------------------------

    def run(
        self,
        cmd: list[str],
        *,
        capture: bool = False,
        quiet: bool = False,
        timeout: int | None = 300,
    ) -> subprocess.CompletedProcess:
        if not quiet:
            print(f"{DIM}$ {shlex.join(cmd)}{RESET}")
        try:
            result = subprocess.run(
                cmd, text=True, capture_output=capture, timeout=timeout, check=False
            )
        except FileNotFoundError as exc:
            raise DockerError(f"Comando no encontrado: {cmd[0]}") from exc
        except subprocess.TimeoutExpired:
            fail(f"El comando tardo demasiado: {shlex.join(cmd)}")
            return subprocess.CompletedProcess(cmd, 124, "", "timeout")
        if capture and result.stdout and not quiet:
            print(result.stdout.rstrip())
        if capture and result.stderr and result.returncode != 0:
            print(result.stderr.rstrip(), file=sys.stderr)
        return result

    def compose_cmd(self) -> list[str]:
        # ['docker', 'compose', ...] o ['docker', 'docker-compose', ...] era el bug
        return self.docker + self.compose

    def comp(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return self.run(self.compose_cmd() + list(args), **kwargs)

    def d(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return self.run(self.docker + list(args), **kwargs)

    def sh(self, container: str, script: str, **kwargs) -> subprocess.CompletedProcess:
        return self.d("exec", container, "sh", "-c", script, **kwargs)

    def output(self, *args: str) -> str:
        result = self.d(*args, capture=True, quiet=True, timeout=30)
        return result.stdout.strip()

    def is_running(self, container: str) -> bool:
        return self.output("inspect", "-f", "{{.State.Running}}", container) == "true"

    def require_up(self) -> bool:
        if self.is_running("pc_oficina") and self.is_running("router_nat"):
            return True
        warn("El laboratorio no esta levantado.")
        if not sys.stdin.isatty():
            warn("Ejecuta primero: python3 demo_redes.py up")
            return False
        answer = input("  ¿Levantarlo ahora? [S/n] ").strip().lower()
        if answer in ("", "s", "si", "y", "yes"):
            action_up(self)
            return True
        return False


# ---------------------------------------------------------------------------
# Acciones
# ---------------------------------------------------------------------------

def action_up(lab: Lab) -> None:
    title("1. Levantando el laboratorio")
    explain("""
Se crean dos redes y cinco contenedores:
  red_oficina 192.168.10.0/24  -> dhcp_server .2, nodo_red .3, router .254
  red_externa 10.99.0.0/24     -> router .254, servidor_externo .2
  pc_oficina no tiene IP fija: la obtiene por DHCP (reservada a .50).
""")
    lab.comp("up", "-d", "--build")

    print()
    print("  Esperando a que pc_oficina complete el DHCP...")
    deadline = time.time() + 60
    ip = ""
    while time.time() < deadline:
        if lab.is_running("pc_oficina"):
            raw = lab.output("exec", "pc_oficina", "ip", "-4", "-o", "addr", "show", "eth0")
            if "192.168.10." in raw:
                ip = raw.split()[3].split("/")[0]
                break
        print(".", end="", flush=True)
        time.sleep(2)
    print()

    if ip:
        ok(f"pc_oficina obtuvo la IP {ip} por DHCP")
    else:
        fail("No se pudo confirmar la IP del cliente. Revisa: docker logs pc_oficina")


def action_status(lab: Lab) -> None:
    title("2. Estado del laboratorio")
    lab.comp("ps")
    for container in ("dhcp_server", "router_nat", "pc_oficina", "nodo_red", "servidor_externo"):
        if not lab.is_running(container):
            warn(f"{container} no esta corriendo")
            continue
        data = lab.output("exec", container, "ip", "-brief", "-4", "addr", "show")
        print(f"\n  {BOLD}{container}{RESET}")
        for line in data.splitlines():
            print(f"    {line}")
    print()


def action_dhcp(lab: Lab) -> None:
    if not lab.require_up():
        return
    title("3. DHCP: configuracion que recibio el cliente")

    step("a", "IP y MAC de pc_oficina (lo entrego el servidor DHCP)")
    explain("""
La interfaz eth0 tiene una IP del rango 192.168.10.50-80 y la MAC que
definimos en el compose. Antes de pedir DHCP, el contenedor borro la IP
que Docker le habia asignado automaticamente.
""")
    lab.d("exec", "pc_oficina", "ip", "addr", "show", "eth0")

    step("b", "Ruta por defecto (gateway entregado en la opcion 3)")
    explain("""
El cliente no configura su gateway a mano: lo recibe junto con la IP.
Apunta a router_nat (192.168.10.254), la unica salida de la red interna.
""")
    lab.d("exec", "pc_oficina", "ip", "route")

    step("c", "Concesiones (leases) del lado del servidor")
    explain("""
dnsmasq guarda cada concesion en /tmp/dnsmasq.leases:
  expira  MAC  IP  nombre  client-id
""")
    lab.d("exec", "dhcp_server", "cat", "/tmp/dnsmasq.leases")

    step("d", "Ultimos mensajes del servidor DHCP")
    lab.d("logs", "--tail", "15", "dhcp_server")


def action_dora(lab: Lab) -> None:
    if not lab.require_up():
        return
    title("4. DHCP: capturando el intercambio DORA")

    explain("""
DHCP usa 4 mensajes (DORA):
  1. DISCOVER  el cliente pregunta en broadcast si hay servidores DHCP
  2. OFFER     el servidor ofrece una IP libre
  3. REQUEST   el cliente pide formalmente esa IP
  4. ACK       el servidor confirma la concesion
Como el cliente todavia no tiene IP, los mensajes van por broadcast
(destino 255.255.255.255), por eso tcpdump los puede ver en eth0.
""")

    step("1/5", "Iniciando captura con tcpdump dentro de pc_oficina")
    lab.d(
        "exec", "-d", "pc_oficina", "sh", "-c",
        "rm -f /tmp/dora.pcap; exec tcpdump -i eth0 -w /tmp/dora.pcap 'udp port 67 or udp port 68'",
    )
    time.sleep(1.5)

    step("2/5", "Borrando la IP actual y pidiendo una nueva por DHCP")
    explain("Al borrar la IP, forzamos el ciclo completo desde DISCOVER.")
    lab.sh("pc_oficina", "ip addr flush dev eth0; ip link set eth0 up; "
                         "udhcpc -i eth0 -n -q -f -s /opt/pc/udhcpc.script")

    step("3/5", "Deteniendo la captura")
    lab.sh("pc_oficina", "pkill -INT tcpdump; sleep 1")

    step("4/5", "Paquetes DHCP capturados (DORA)")
    explain("""
En la salida se ven los 4 mensajes. Fijate que:
  * DISCOVER y REQUEST salen del puerto 68 hacia el 67 (broadcast)
  * OFFER y ACK salen del puerto 67 hacia el 68
  * el xid (identificador de transaccion) es el mismo en los cuatro
""")
    lab.d("exec", "pc_oficina", "tcpdump", "-nn", "-r", "/tmp/dora.pcap")

    step("5/5", "Registro del servidor DHCP (lado servidor)")
    lab.d("logs", "--tail", "25", "dhcp_server")

    print()
    ok("La IP se renovo y sigue siendo la misma (el servidor recuerda la concesion por MAC).")
    lab.d("exec", "pc_oficina", "ip", "-4", "-brief", "addr", "show", "eth0")


def action_nat(lab: Lab) -> None:
    if not lab.require_up():
        return
    title("5. NAT: reglas configuradas en el router")

    step("a", "SNAT / MASQUERADE (salida de la red interna)")
    explain("""
POSTROUTING se aplica justo antes de enviar el paquete. La regla
MASQUERADE reemplaza la IP privada de origen (192.168.10.x) por la IP
externa del router (10.99.0.254). La columna pkts cuenta los paquetes
que pasaron por la regla.
""")
    lab.d("exec", "router_nat", "iptables", "-t", "nat", "-L", "POSTROUTING", "-v", "-n")

    step("b", "DNAT (publicar un servicio interno)")
    explain("""
PREROUTING se aplica al paquete que llega. La regla DNAT redirige el
puerto 8080 del router hacia 192.168.10.50:8000. Es el "port forwarding"
que se usa para publicar un servidor interno en Internet.
""")
    lab.d("exec", "router_nat", "iptables", "-t", "nat", "-L", "PREROUTING", "-v", "-n")

    step("c", "Reenvio IPv4 (sin esto no hay NAT)")
    lab.d("exec", "router_nat", "sysctl", "net.ipv4.ip_forward")
    print()
    ok("El router reenvia paquetes entre las dos redes y traduce direcciones.")


def action_traffic(lab: Lab) -> None:
    if not lab.require_up():
        return
    title("6. NAT en accion: trafico interno -> externo")

    explain("""
Vamos a pedir una pagina al servidor externo (10.99.0.2:8000) desde
pc_oficina (IP privada). Al mismo tiempo capturamos con tcpdump en el
servidor externo: ahi se ve con que IP llega el paquete. Si el NAT
funciona, la IP de origen NO sera 192.168.10.50 sino la del router.
""")

    step("1/5", "Reglas y contadores ANTES del trafico")
    lab.d("exec", "router_nat", "iptables", "-t", "nat", "-L", "POSTROUTING", "-v", "-n")

    step("2/5", "Iniciando captura en el servidor externo")
    lab.d(
        "exec", "-d", "servidor_externo", "sh", "-c",
        "rm -f /tmp/nat.pcap; exec tcpdump -i eth0 -w /tmp/nat.pcap 'tcp port 8000'",
    )
    time.sleep(1.5)

    step("3/5", "Solicitando la pagina desde pc_oficina")
    lab.d(
        "exec", "pc_oficina", "curl", "-sS", "-o", "/tmp/respuesta.html",
        "-w", "HTTP %{http_code} - respuesta de %{remote_ip}:%{remote_port} en %{time_total}s\\n",
        "http://10.99.0.2:8000/",
    )
    lab.sh("pc_oficina", "grep -o '<title>[^<]*</title>' /tmp/respuesta.html || true")

    step("4/5", "Deteniendo la captura y leyendo los paquetes")
    lab.sh("servidor_externo", "pkill -INT tcpdump; sleep 1")
    explain("""
Mira la columna de origen: los paquetes llegan desde 10.99.0.254 (la IP
del router), no desde 192.168.10.50. Eso es exactamente lo que hace el
MASQUERADE. La respuesta vuelve al router, que recuerda la traduccion y
se la entrega al cliente.
""")
    lab.d("exec", "servidor_externo", "tcpdump", "-nn", "-r", "/tmp/nat.pcap", "-c", "8")

    step("5/5", "Contadores DESPUES del trafico y log del servidor")
    lab.d("exec", "router_nat", "iptables", "-t", "nat", "-L", "POSTROUTING", "-v", "-n")
    lab.d("logs", "--tail", "5", "servidor_externo")

    print()
    ok("El trafico salio con la IP del router: NAT comprobado.")


def action_conntrack(lab: Lab) -> None:
    if not lab.require_up():
        return
    title("7. NAT: tabla de conexiones (conntrack)")
    explain("""
El router recuerda cada traduccion en la tabla de conntrack para poder
devolver las respuestas al cliente correcto. Cada linea muestra:
  src=IP:puerto_privada  dst=IP:puerto_destino
  src=IP:puerto_publica  dst=IP:puerto_origen
""")
    lab.sh(
        "router_nat",
        "cat /proc/net/nf_conntrack 2>/dev/null | grep -E '8000|8080' | head -20 || true",
    )
    print()
    explain("""
Si la tabla aparece vacia, no es un error del laboratorio: algunos kernels
no exponen /proc/net/nf_conntrack dentro del contenedor. En ese caso los
contadores de iptables siguen siendo la prueba (accion 'nat').
""")


def action_dnat(lab: Lab) -> None:
    if not lab.require_up():
        return
    title("8. DNAT: acceder a un servidor interno desde afuera")
    explain("""
pc_oficina corre un servidor web en el puerto 8000, pero es una IP privada:
desde la red externa no se puede llegar a 192.168.10.50 directamente.
El router publica ese servicio en 10.99.0.254:8080 mediante DNAT.
Vamos a pedirlo desde el servidor externo (como si fueramos Internet).
""")

    step("1/3", "Regla DNAT del router")
    lab.d("exec", "router_nat", "iptables", "-t", "nat", "-L", "PREROUTING", "-v", "-n")

    step("2/3", "Peticion desde la red externa a 10.99.0.254:8080")
    lab.d(
        "exec", "servidor_externo", "curl", "-sS", "-o", "/tmp/interno.html",
        "-w", "HTTP %{http_code} - respuesta de %{remote_ip}:%{remote_port}\\n",
        "http://10.99.0.254:8080/",
    )
    lab.sh("servidor_externo", "grep -o '<title>[^<]*</title>' /tmp/interno.html || true")

    step("3/3", "Log del servidor interno: quien lo visito")
    explain("""
El DNAT cambia la IP de destino a 192.168.10.50 antes de reenviar el
paquete. El servidor interno ve la conexion como si viniera del router.
""")
    lab.sh("pc_oficina", "tail -3 /tmp/http.log || true")

    print()
    ok("Se llego a un servidor privado desde la red externa usando DNAT.")


def action_ping(lab: Lab) -> None:
    if not lab.require_up():
        return
    title("9. Conectividad")
    step("a", "pc_oficina -> nodo_red (dentro de la red local, sin NAT)")
    lab.d("exec", "pc_oficina", "ping", "-c", "3", "nodo_red")

    step("b", "pc_oficina -> servidor_externo (pasa por el router y el NAT)")
    lab.d("exec", "pc_oficina", "ping", "-c", "3", "10.99.0.2")

    step("c", "pc_oficina -> 8.8.8.8 (Internet)")
    explain("""
Este ping depende de que la computadora tenga salida a Internet y de que
la red no bloquee ICMP. Si falla, no afecta a la demo: los pasos
anteriores ya prueban el NAT.
""")
    lab.d("exec", "pc_oficina", "ping", "-c", "2", "-W", "2", "8.8.8.8")


def action_down(lab: Lab) -> None:
    title("10. Apagando y limpiando el laboratorio")
    lab.comp("down")
    ok("Contenedores y redes eliminados.")


ACTIONS = {
    "up": action_up,
    "status": action_status,
    "dhcp": action_dhcp,
    "dora": action_dora,
    "nat": action_nat,
    "traffic": action_traffic,
    "conntrack": action_conntrack,
    "dnat": action_dnat,
    "ping": action_ping,
    "down": action_down,
}

MENU = [
    ("1", "up", "Levantar el laboratorio"),
    ("2", "status", "Ver estado e IPs de los contenedores"),
    ("3", "dhcp", "DHCP: IP, ruta y concesiones del cliente"),
    ("4", "dora", "DHCP: capturar el intercambio DORA"),
    ("5", "nat", "NAT: reglas y contadores del router"),
    ("6", "traffic", "NAT: generar trafico y capturarlo"),
    ("7", "conntrack", "NAT: tabla de conexiones"),
    ("8", "dnat", "DNAT: acceder a un servidor interno desde afuera"),
    ("9", "ping", "Probar conectividad"),
    ("10", "down", "Apagar y limpiar"),
]

FULL_DEMO = ["up", "dhcp", "dora", "nat", "traffic", "conntrack", "dnat", "ping"]


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def run_action(lab: Lab, name: str, pause: bool) -> None:
    try:
        ACTIONS[name](lab)
    except DockerError as exc:
        fail(str(exc))
        return
    except KeyboardInterrupt:
        print("\n  Interrumpido por el usuario.")
        return
    if pause:
        input(f"\n  {DIM}[Enter] para continuar...{RESET}")


def menu(lab: Lab, pause: bool) -> None:
    while True:
        title("Laboratorio DHCP + NAT  |  Redes I")
        for number, _name, label in MENU:
            print(f"  {number:>2}) {label}")
        print("   0) Salir")
        try:
            choice = input("\n  Opcion: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("0", "q", "salir"):
            return
        match = next((name for number, name, _ in MENU if number == choice), None)
        if match:
            run_action(lab, match, pause)
        else:
            warn("Opcion invalida")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Demo interactiva del laboratorio DHCP + NAT sobre Docker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Acciones: " + ", ".join(ACTIONS) + ", all",
    )
    parser.add_argument("accion", nargs="?", default=None,
                        help="accion a ejecutar (si se omite, abre el menu)")
    parser.add_argument("--sin-pausa", action="store_true",
                        help="no detenerse entre acciones")
    args = parser.parse_args(argv)

    try:
        lab = Lab()
    except DockerError as exc:
        fail(str(exc))
        return 1

    pause = not args.sin_pausa and sys.stdin.isatty()

    if args.accion is None:
        menu(lab, pause)
        return 0

    if args.accion == "all":
        for name in FULL_DEMO:
            run_action(lab, name, pause)
        print()
        ok("Recorrido completo terminado. El laboratorio sigue levantado.")
        print("  Para apagarlo: python3 demo_redes.py down")
        return 0

    if args.accion not in ACTIONS:
        fail(f"Accion desconocida: {args.accion}")
        print("  Acciones validas: " + ", ".join(ACTIONS) + ", all")
        return 2

    run_action(lab, args.accion, pause=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
