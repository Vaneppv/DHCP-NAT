#!/bin/sh
# PC cliente del laboratorio.
#   1. Borra la IP que Docker le asigno automaticamente.
#   2. Pide configuracion por DHCP con udhcpc (cliente real de BusyBox).
#   3. Levanta un servidor web interno para demostrar el DNAT del router.

set -eu

IFACE="${IFACE:-eth0}"

echo "[pc] IP inicial asignada por Docker (se descarta):"
ip -brief addr show dev "$IFACE" || true

ip addr flush dev "$IFACE"
ip link set "$IFACE" up

echo "[pc] Enviando DHCPDISCOVER (udhcpc)..."
ATTEMPT=1
DHCP_OK=0
while [ "$ATTEMPT" -le 20 ]; do
    if udhcpc -i "$IFACE" -n -q -f -s /opt/pc/udhcpc.script; then
        DHCP_OK=1
        break
    fi
    echo "[pc] Sin respuesta del servidor DHCP (intento $ATTEMPT/20); reintento en 2 s..."
    ATTEMPT=$((ATTEMPT + 1))
    sleep 2
done

if [ "$DHCP_OK" -ne 1 ]; then
    echo "[pc] ADVERTENCIA: no se obtuvo configuracion por DHCP" >&2
fi

echo "[pc] Configuracion obtenida:"
ip -brief addr show dev "$IFACE"
ip route

echo "[pc] Servidor web interno en 0.0.0.0:8000 (para la demo de DNAT)"
python3 -m http.server 8000 --directory /srv >/tmp/http.log 2>&1 &

exec sleep infinity
