"""Codificacion y decodificacion de mensajes DHCPv4 (RFC 2131 / RFC 2132).

Todo el protocolo se arma "a mano" con struct y socket: no se usa ninguna
libreria externa. Esto permite mostrar en clase, byte por byte, que viaja
realmente en los cuatro mensajes del intercambio DORA:

    DHCPDISCOVER  ->  cliente busca un servidor DHCP
    DHCPOFFER     <-  el servidor ofrece una IP
    DHCPREQUEST   ->  el cliente pide formalmente esa IP
    DHCPACK       <-  el servidor confirma la concesion ("lease")

Estructura de un mensaje DHCP (RFC 2131, seccion 2):

    0                   1                   2                   3
    0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
   +---------------+---------------+---------------+---------------+
   |     op        |     htype     |     hlen      |     hops      |
   +---------------+---------------+---------------+---------------+
   |                            xid                                |
   +-------------------------------+-------------------------------+
   |            secs               |            flags              |
   +-------------------------------+-------------------------------+
   |                          ciaddr                               |
   +---------------------------------------------------------------+
   |                          yiaddr                               |
   +---------------------------------------------------------------+
   |                          siaddr                               |
   +---------------------------------------------------------------+
   |                          giaddr                               |
   +---------------------------------------------------------------+
   |                          chaddr (16 bytes)                    |
   |                                                               |
   +---------------------------------------------------------------+
   |                          sname  (64 bytes)                    |
   +---------------------------------------------------------------+
   |                          file  (128 bytes)                    |
   +---------------------------------------------------------------+
   |                    cookie magico 0x63825363                   |
   +---------------------------------------------------------------+
   |                     opciones (formato TLV)                    |
   +---------------------------------------------------------------+
"""

from __future__ import annotations

import random
import socket
import struct

# ---------------------------------------------------------------------------
# Constantes del protocolo
# ---------------------------------------------------------------------------

MAGIC_COOKIE = b"\x63\x82\x53\x63"  # 99.130.83.99, identifica un mensaje DHCP

BOOTREQUEST = 1
BOOTREPLY = 2

# Tipos de mensaje DHCP (opcion 53)
DISCOVER = 1
OFFER = 2
REQUEST = 3
DECLINE = 4
ACK = 5
NAK = 6
RELEASE = 7
INFORM = 8

TYPE_NAMES = {
    DISCOVER: "DHCPDISCOVER",
    OFFER: "DHCPOFFER",
    REQUEST: "DHCPREQUEST",
    DECLINE: "DHCPDECLINE",
    ACK: "DHCPACK",
    NAK: "DHCPNAK",
    RELEASE: "DHCPRELEASE",
    INFORM: "DHCPINFORM",
}

# Codigos de opcion que usamos
OPT_PAD = 0
OPT_SUBNET_MASK = 1
OPT_ROUTER = 3
OPT_DNS = 6
OPT_HOSTNAME = 12
OPT_DOMAIN = 15
OPT_BROADCAST = 28
OPT_REQUESTED_IP = 50
OPT_LEASE_TIME = 51
OPT_MSG_TYPE = 53
OPT_SERVER_ID = 54
OPT_PARAM_LIST = 55
OPT_MAX_SIZE = 57
OPT_T1 = 58
OPT_T2 = 59
OPT_CLIENT_ID = 61
OPT_END = 255

OPTION_NAMES = {
    OPT_SUBNET_MASK: "Mascara de subred",
    OPT_ROUTER: "Router (gateway)",
    OPT_DNS: "Servidores DNS",
    OPT_HOSTNAME: "Nombre del host",
    OPT_DOMAIN: "Dominio",
    OPT_BROADCAST: "Direccion de broadcast",
    OPT_REQUESTED_IP: "IP solicitada",
    OPT_LEASE_TIME: "Tiempo de concesion",
    OPT_MSG_TYPE: "Tipo de mensaje",
    OPT_SERVER_ID: "ID del servidor",
    OPT_PARAM_LIST: "Parametros solicitados",
    OPT_MAX_SIZE: "Tamano maximo",
    OPT_T1: "T1 (renovacion)",
    OPT_T2: "T2 (reenlace)",
    OPT_CLIENT_ID: "Identificador de cliente",
}

# Nombres cortos para la opcion 55 (lista de parametros solicitados)
PARAM_NAMES = {
    1: "mascara",
    3: "router",
    6: "dns",
    12: "hostname",
    15: "dominio",
    28: "broadcast",
    42: "ntp",
    51: "lease",
    54: "server-id",
    58: "T1",
    59: "T2",
    119: "dominios-busqueda",
    121: "rutas",
}

_FIXED = struct.Struct("!BBBBIHH4s4s4s4s16s64s128s")
assert _FIXED.size == 236


# ---------------------------------------------------------------------------
# Utilidades de conversion
# ---------------------------------------------------------------------------

def ip_to_int(ip: str) -> int:
    """Convierte '192.168.10.50' al entero 3232238130 (para recorrer rangos)."""
    return struct.unpack("!I", socket.inet_aton(ip))[0]


def int_to_ip(value: int) -> str:
    """Inverso de ip_to_int."""
    return socket.inet_ntoa(struct.pack("!I", value))


def mac_to_bytes(mac: str) -> bytes:
    """Convierte '02:42:ac:11:00:50' a sus 6 bytes."""
    return bytes(int(part, 16) for part in mac.replace("-", ":").split(":"))


def bytes_to_mac(raw: bytes) -> str:
    """Convierte 6 bytes a la representacion 'aa:bb:cc:dd:ee:ff'."""
    return ":".join(f"{byte:02x}" for byte in raw[:6])


def hexdump(data: bytes, prefix: str = "    ") -> str:
    """Volcado hexadecimal con la parte ASCII, estilo 'hexdump -C'."""
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{prefix}{offset:04x}  {hex_part:<47}  |{ascii_part}|")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mensaje DHCP
# ---------------------------------------------------------------------------

class DHCPMessage:
    """Representa un mensaje DHCP completo (cabecera BOOTP + opciones)."""

    def __init__(
        self,
        *,
        op: int = BOOTREQUEST,
        xid: int | None = None,
        secs: int = 0,
        flags: int = 0,
        ciaddr: str = "0.0.0.0",
        yiaddr: str = "0.0.0.0",
        siaddr: str = "0.0.0.0",
        giaddr: str = "0.0.0.0",
        chaddr: bytes = b"",
        sname: bytes = b"",
        file: bytes = b"",
        options: dict[int, bytes] | None = None,
    ) -> None:
        self.op = op
        # xid: identificador de transaccion; cliente y servidor usan el mismo
        self.xid = xid if xid is not None else random.getrandbits(32)
        self.secs = secs
        self.flags = flags
        self.ciaddr = ciaddr
        self.yiaddr = yiaddr
        self.siaddr = siaddr
        self.giaddr = giaddr
        self.chaddr = (chaddr or b"").ljust(16, b"\x00")[:16]
        self.sname = (sname or b"").ljust(64, b"\x00")[:64]
        self.file = (file or b"").ljust(128, b"\x00")[:128]
        self.options: dict[int, bytes] = dict(options or {})

    # -- propiedades comodas -------------------------------------------------

    @property
    def msg_type(self) -> int | None:
        raw = self.options.get(OPT_MSG_TYPE)
        return raw[0] if raw else None

    @msg_type.setter
    def msg_type(self, value: int) -> None:
        self.options[OPT_MSG_TYPE] = bytes([value])

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.msg_type, f"DESCONOCIDO({self.msg_type})")

    @property
    def mac(self) -> str:
        return bytes_to_mac(self.chaddr)

    @property
    def broadcast_flag(self) -> bool:
        # El bit mas alto de 'flags' indica "responder por broadcast"
        return bool(self.flags & 0x8000)

    # -- helpers para construir opciones -------------------------------------

    def set_ip(self, code: int, ip: str) -> None:
        self.options[code] = socket.inet_aton(ip)

    def set_ips(self, code: int, ips: list[str]) -> None:
        self.options[code] = b"".join(socket.inet_aton(ip) for ip in ips)

    def set_u32(self, code: int, value: int) -> None:
        self.options[code] = struct.pack("!I", value)

    def set_u16(self, code: int, value: int) -> None:
        self.options[code] = struct.pack("!H", value)

    def set_str(self, code: int, value: str) -> None:
        self.options[code] = value.encode()

    def set_bytes(self, code: int, value: bytes) -> None:
        self.options[code] = value

    def get_ip(self, code: int) -> str | None:
        raw = self.options.get(code)
        return socket.inet_ntoa(raw[:4]) if raw and len(raw) >= 4 else None

    def get_ips(self, code: int) -> list[str]:
        raw = self.options.get(code) or b""
        return [socket.inet_ntoa(raw[i:i + 4]) for i in range(0, len(raw) - 3, 4)]

    def get_u32(self, code: int) -> int | None:
        raw = self.options.get(code)
        return struct.unpack("!I", raw[:4])[0] if raw and len(raw) >= 4 else None

    def get_u16(self, code: int) -> int | None:
        raw = self.options.get(code)
        return struct.unpack("!H", raw[:2])[0] if raw and len(raw) >= 2 else None

    def get_str(self, code: int) -> str:
        raw = self.options.get(code) or b""
        return raw.decode(errors="replace").strip("\x00")

    # -- serializacion -------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Convierte el objeto a los bytes que viajan por UDP."""
        fixed = _FIXED.pack(
            self.op,
            1,  # htype: Ethernet
            6,  # hlen: MAC = 6 bytes
            0,  # hops
            self.xid,
            self.secs,
            self.flags,
            socket.inet_aton(self.ciaddr),
            socket.inet_aton(self.yiaddr),
            socket.inet_aton(self.siaddr),
            socket.inet_aton(self.giaddr),
            self.chaddr,
            self.sname,
            self.file,
        )
        encoded = bytearray(MAGIC_COOKIE)
        for code, value in self.options.items():
            if not value:
                continue
            if len(value) > 255:
                # RFC 3396: opciones largas se parten en varios TLV
                for offset in range(0, len(value), 255):
                    chunk = value[offset:offset + 255]
                    encoded.append(code)
                    encoded.append(len(chunk))
                    encoded.extend(chunk)
            else:
                encoded.append(code)
                encoded.append(len(value))
                encoded.extend(value)
        encoded.append(OPT_END)
        return fixed + bytes(encoded)

    @classmethod
    def from_bytes(cls, data: bytes) -> "DHCPMessage":
        """Parsea los bytes recibidos por el socket."""
        if len(data) < 240:
            raise ValueError(f"mensaje DHCP demasiado corto ({len(data)} bytes)")
        if data[236:240] != MAGIC_COOKIE:
            raise ValueError("cookie DHCP invalido (no parece un mensaje DHCP)")

        (op, _htype, _hlen, _hops, xid, secs, flags,
         ciaddr, yiaddr, siaddr, giaddr, chaddr, sname, file) = _FIXED.unpack(data[:236])

        msg = cls(
            op=op,
            xid=xid,
            secs=secs,
            flags=flags,
            ciaddr=socket.inet_ntoa(ciaddr),
            yiaddr=socket.inet_ntoa(yiaddr),
            siaddr=socket.inet_ntoa(siaddr),
            giaddr=socket.inet_ntoa(giaddr),
            chaddr=chaddr,
            sname=sname,
            file=file,
        )

        # Opciones: secuencia de (codigo, largo, valor). 0 = PAD, 255 = END.
        index = 240
        while index < len(data):
            code = data[index]
            if code == OPT_END:
                break
            if code == OPT_PAD:
                index += 1
                continue
            if index + 1 >= len(data):
                break
            length = data[index + 1]
            value = data[index + 2:index + 2 + length]
            # RFC 3396: concatenar fragmentos de la misma opcion
            msg.options[code] = msg.options.get(code, b"") + value
            index += 2 + length
        return msg

    # -- representacion legible ----------------------------------------------

    def format_option(self, code: int, value: bytes) -> str:
        """Devuelve el valor de una opcion en formato humano."""
        if code == OPT_MSG_TYPE and value:
            return TYPE_NAMES.get(value[0], f"tipo {value[0]}")
        if code in (OPT_SUBNET_MASK, OPT_REQUESTED_IP, OPT_SERVER_ID, OPT_BROADCAST):
            return socket.inet_ntoa(value[:4]) if len(value) >= 4 else value.hex()
        if code in (OPT_ROUTER, OPT_DNS):
            return ", ".join(
                socket.inet_ntoa(value[i:i + 4]) for i in range(0, len(value) - 3, 4)
            )
        if code in (OPT_LEASE_TIME, OPT_T1, OPT_T2) and len(value) >= 4:
            seconds = struct.unpack("!I", value[:4])[0]
            return f"{seconds} s ({seconds // 3600} h)"
        if code == OPT_PARAM_LIST:
            return ", ".join(PARAM_NAMES.get(byte, str(byte)) for byte in value)
        if code in (OPT_HOSTNAME, OPT_DOMAIN):
            return value.decode(errors="replace")
        if code == OPT_MAX_SIZE and len(value) >= 2:
            return f"{struct.unpack('!H', value[:2])[0]} bytes"
        if code == OPT_CLIENT_ID:
            return "0x" + value.hex()
        return "0x" + value.hex()

    def pretty(self) -> str:
        """Representacion multilinea pensada para mostrar en consola."""
        title = self.type_name
        lines = [
            "+-- " + title + " " + "-" * max(0, 58 - len(title)),
            f"| xid      : 0x{self.xid:08x}   (identificador de la transaccion)",
            f"| cliente  : {self.mac}   (campo chaddr)",
            f"| flags    : {'broadcast' if self.broadcast_flag else 'unicast'} (0x{self.flags:04x})",
            f"| ciaddr   : {self.ciaddr:<15}  yiaddr : {self.yiaddr}",
            f"| siaddr   : {self.siaddr:<15}  giaddr : {self.giaddr}",
        ]
        if self.options:
            lines.append("| opciones :")
            for code in sorted(self.options):
                name = OPTION_NAMES.get(code, f"Opcion {code}")
                lines.append(f"|   {code:3d} {name:<24}: {self.format_option(code, self.options[code])}")
        lines.append("+" + "-" * 63)
        return "\n".join(lines)

    def __repr__(self) -> str:  # pragma: no cover - solo para depuracion
        return f"<DHCPMessage {self.type_name} xid=0x{self.xid:08x} mac={self.mac}>"
