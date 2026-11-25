#!/bin/sh
# Servidor DHCP del laboratorio: dnsmasq atendiendo la red de oficina.
set -eu

IFACE="${DHCP_IFACE:-eth0}"

echo "[dhcp] Iniciando servidor DHCP (dnsmasq) en $IFACE"
echo "[dhcp] Rango: 192.168.10.50 - 192.168.10.80"
echo "[dhcp] Gateway: 192.168.10.254   DNS: 8.8.8.8, 1.1.1.1"
echo "[dhcp] Reserva fija: 02:42:ac:11:00:50 -> 192.168.10.50 (pc_oficina)"

# --port=0 desactiva el DNS de dnsmasq: solo nos interesa el DHCP.
# --conf-file=/dev/null ignora /etc/dnsmasq.conf y usa solo estos parametros.
exec dnsmasq \
    --no-daemon \
    --conf-file=/dev/null \
    --port=0 \
    --interface="$IFACE" \
    --bind-interfaces \
    --dhcp-authoritative \
    --dhcp-range=192.168.10.50,192.168.10.80,255.255.255.0,12h \
    --dhcp-option=option:router,192.168.10.254 \
    --dhcp-option=option:dns-server,8.8.8.8,1.1.1.1 \
    --dhcp-option=option:domain-name,oficina.local \
    --dhcp-host=02:42:ac:11:00:50,192.168.10.50,pc-oficina,12h \
    --dhcp-leasefile=/tmp/dnsmasq.leases \
    --log-dhcp \
    --log-facility=-
