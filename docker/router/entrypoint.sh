#!/bin/sh
# Router del laboratorio: reenvia paquetes entre la red de oficina y la
# red externa, aplica NAT (MASQUERADE) a la salida y publica un servicio
# interno mediante DNAT (port forwarding).
set -eu

echo "[router] Iniciando router NAT"

# 1) Reenvio IPv4. Docker ya lo activa con sysctls, pero lo dejamos explicito
#    porque es el requisito sin el cual el NAT no funciona.
if ! sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1; then
    echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null \
        || echo "[router] aviso: no se pudo escribir ip_forward (Docker ya lo activo via sysctls)"
fi

# 2) Detectar las interfaces por su direccion (no asumimos eth0/eth1).
INT_IF=$(ip -o -4 addr show | awk '$4 ~ /^192\.168\.10\./ {print $2; exit}')
EXT_IF=$(ip -o -4 addr show | awk '$4 ~ /^10\.99\./ {print $2; exit}')
INT_IF=${INT_IF:-eth0}
EXT_IF=${EXT_IF:-eth1}

echo "[router] Interfaz interna : $INT_IF (192.168.10.0/24)"
echo "[router] Interfaz externa : $EXT_IF (10.99.0.0/24)"

# 3) Elegir el backend de iptables disponible en el contenedor.
IPT=""
if iptables -t nat -L -n >/dev/null 2>&1; then
    IPT="iptables"
elif command -v iptables-legacy >/dev/null 2>&1 && iptables-legacy -t nat -L -n >/dev/null 2>&1; then
    IPT="iptables-legacy"
else
    echo "[router] ERROR: no se pudo inicializar iptables (probar en el host: modprobe nf_nat)" >&2
    exit 1
fi
echo "[router] Usando: $IPT"

# 4) Politica de reenvio y reglas de filtrado.
#    La politica ACCEPT ya permite el reenvio; las reglas siguientes lo dejan
#    explicito y son las que se muestran en clase.
$IPT -P FORWARD ACCEPT
$IPT -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
$IPT -A FORWARD -i "$INT_IF" -o "$EXT_IF" -j ACCEPT 2>/dev/null || true
$IPT -A FORWARD -i "$EXT_IF" -o "$INT_IF" -j ACCEPT 2>/dev/null || true

# 5) SNAT / MASQUERADE: todo lo que sale de la red interna hacia Internet
#    se reescribe con la IP externa del router. Asi miles de hosts privados
#    comparten una sola IP publica.
$IPT -t nat -A POSTROUTING -s 192.168.10.0/24 -o "$EXT_IF" -j MASQUERADE

# 6) DNAT: publicar el servidor web interno (192.168.10.50:8000) en el
#    puerto 8080 del router. Es el "port forwarding" clasico del NAT.
$IPT -t nat -A PREROUTING -i "$EXT_IF" -p tcp --dport 8080 -j DNAT --to-destination 192.168.10.50:8000

# 7) Ruta por defecto hacia la red externa (para salir a Internet).
ip route replace default via 10.99.0.1 dev "$EXT_IF"

echo "[router] Reglas NAT activas:"
$IPT -t nat -L -v -n
echo "[router] Router listo"

exec sleep infinity
