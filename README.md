# DHCP y NAT — Proyecto de Redes I

Laboratorio reproducible con **Docker** y una **implementación propia en Python**
de los dos protocolos:

- **DHCP** (Dynamic Host Configuration Protocol): cómo un equipo sin configuración
  obtiene IP, máscara, gateway y DNS automáticamente.
- **NAT** (Network Address Translation): cómo muchos hosts con IP privada salen a
  Internet compartiendo una única IP pública, y cómo se publica un servidor interno.

El laboratorio se puede usar en la exposición con un solo comando y también
permite mostrar el protocolo "por dentro" con sockets reales y volcados
hexadecimales de los paquetes.

---

## 1. Arquitectura del laboratorio

```
                         red_oficina  192.168.10.0/24
  +----------------+                                          +-------------------+
  | dhcp_server    |  .2                                      | router_nat        |
  | (dnsmasq)      |------------------------------------------| .254  (eth0)      |
  +----------------+                                          |                   |
                                                              |  MASQUERADE       |
  +----------------+                                          |  DNAT :8080       |
  | nodo_red       |  .3                                      |        (eth1) .254|
  +----------------+                                          +---------+---------+
                                                                        |
  +----------------+                                                  |  red_externa
  | pc_oficina     |  .50 (por DHCP, reservada)                       |  10.99.0.0/24
  | (udhcpc)       |--------------------------------------------------+
  | web interno    |                                                  |
  +----------------+                                        +---------+---------+
                                                            | servidor_externo  |
                                                            | .2 (web :8000)    |
                                                            +-------------------+
```

| Contenedor         | Rol                                   | Dirección              |
|--------------------|---------------------------------------|------------------------|
| `dhcp_server`      | Servidor DHCP (dnsmasq)               | `192.168.10.2`         |
| `router_nat`       | Router + NAT (SNAT y DNAT)            | `.254` en cada red     |
| `pc_oficina`       | Cliente DHCP + servidor web interno   | `192.168.10.50` (DHCP) |
| `nodo_red`         | Host interno para pruebas             | `192.168.10.3`         |
| `servidor_externo` | Simula un servidor de Internet        | `10.99.0.2`            |

---

## 2. Requisitos

- **Docker** con Compose v2 (`docker compose version`).
  - Windows/macOS: Docker Desktop iniciado.
  - Linux: servicio activo y el usuario con permiso sobre el socket. Si al
    ejecutar aparece `permission denied`, hacer una vez:
    ```bash
    sudo usermod -aG docker $USER   # cerrar sesión y volver a entrar
    ```
    (o ejecutar los comandos con `sudo`).
- **Python 3.8+** solo para `demo_redes.py` y la implementación propia.
  El laboratorio en sí funciona sin Python en el host.

---

## 3. Inicio rápido

```bash
docker compose up -d --build     # construye la imagen y levanta todo
python3 demo_redes.py            # menú interactivo con la demo completa
```

O directamente el recorrido completo, sin menú:

```bash
python3 demo_redes.py all
```

Para apagar y limpiar:

```bash
docker compose down
# o:  python3 demo_redes.py down
```

> La primera construcción descarga Alpine y las herramientas (necesita
> Internet una vez). Después arranca en segundos.

---

## 4. DHCP: qué se demuestra

### 4.1 Teoría mínima

DHCP usa **UDP**, puerto **67** del servidor y **68** del cliente. El cliente
todavía no tiene IP, así que los dos primeros mensajes van a
`255.255.255.255` (broadcast) en la misma red (dominio de broadcast).
El intercambio se llama **DORA**:

| # | Mensaje    | Dirección         | Contenido                                              |
|---|------------|-------------------|--------------------------------------------------------|
| 1 | `DISCOVER` | cliente → broadcast | "¿Hay algún servidor DHCP?"                          |
| 2 | `OFFER`    | servidor → cliente | "Te ofrezco la IP X" (opción 50/yiaddr)               |
| 3 | `REQUEST`  | cliente → broadcast | "Acepto la IP X del servidor Y" (opciones 50 y 54)   |
| 4 | `ACK`      | servidor → cliente | "Confirmado, es tuya por N segundos" (opción 51)     |

Además de la IP, el servidor entrega:

| Opción | Significado              |
|--------|--------------------------|
| 1      | Máscara de subred        |
| 3      | Gateway (router)         |
| 6      | Servidores DNS           |
| 15     | Dominio                  |
| 51     | Duración de la concesión |
| 54     | IP del servidor DHCP     |

La concesión (*lease*) es temporal: cuando vence, el cliente renueva. El
servidor identifica al cliente por su **MAC** (campo `chaddr`) y por el
**xid** (identificador de transacción), no por su nombre.

### 4.2 En el laboratorio

`dhcp_server` corre **dnsmasq** con el rango `.50–.80` y una reserva fija
para la MAC de `pc_oficina`, que por eso siempre recibe `192.168.10.50`.
`pc_oficina` usa **udhcpc** (el cliente DHCP de BusyBox) y aplica lo recibido
con un script propio: `docker/pc/udhcpc.script`.

Ver la configuración obtenida:

```bash
docker exec pc_oficina ip addr show eth0
docker exec pc_oficina ip route
```

```text
2: eth0@if41: <BROADCAST,MULTICAST,UP,LOWER_UP> ...
    link/ether 02:42:ac:11:00:50 brd ff:ff:ff:ff:ff:ff
    inet 192.168.10.50/24 brd 192.168.10.255 scope global eth0

default via 192.168.10.254 dev eth0
192.168.10.0/24 dev eth0 scope link src 192.168.10.50
```

Ver la concesión del lado del servidor:

```bash
docker exec dhcp_server cat /tmp/dnsmasq.leases
```

```text
1795608678 02:42:ac:11:00:50 192.168.10.50 pc-oficina 01:02:42:ac:11:00:50
# expira     MAC                IP             nombre      client-id
```

Ver el detalle del intercambio en el log de dnsmasq:

```bash
docker logs --tail 20 dhcp_server
```

```text
dnsmasq-dhcp[1]: DHCPDISCOVER(eth0) 02:42:ac:11:00:50
dnsmasq-dhcp[1]: DHCPOFFER(eth0) 192.168.10.50 02:42:ac:11:00:50
dnsmasq-dhcp[1]: DHCPREQUEST(eth0) 192.168.10.50 02:42:ac:11:00:50
dnsmasq-dhcp[1]: DHCPACK(eth0) 192.168.10.50 02:42:ac:11:00:50 pc-oficina
```

### 4.3 Capturar DORA con tcpdump

La forma más contundente de mostrarlo en clase:

```bash
docker exec -d pc_oficina sh -c \
  "tcpdump -i eth0 -w /tmp/dora.pcap 'udp port 67 or udp port 68'"

docker exec pc_oficina sh -c \
  "ip addr flush dev eth0; ip link set eth0 up; udhcpc -i eth0 -n -q -f -s /opt/pc/udhcpc.script"

docker exec pc_oficina sh -c "pkill -INT tcpdump; sleep 1"
docker exec pc_oficina tcpdump -nn -r /tmp/dora.pcap
```

```text
IP 0.0.0.0.68 > 255.255.255.255.67: BOOTP/DHCP, Request from 02:42:ac:11:00:50, length 279
IP 192.168.10.2.67 > 255.255.255.255.68: BOOTP/DHCP, Reply, length 299
IP 0.0.0.0.68 > 255.255.255.255.67: BOOTP/DHCP, Request from 02:42:ac:11:00:50, length 293
IP 192.168.10.2.67 > 255.255.255.255.68: BOOTP/DHCP, Reply, length 311
```

Todo el procedimiento está automatizado en:

```bash
python3 demo_redes.py dora
```

### 4.4 ¿Por qué la IP no es "de Docker"?

Docker le asigna a cada contenedor una IP al crearlo. Para que el DHCP sea
real, `pc_oficina` la **borra al arrancar** (`ip addr flush dev eth0`) y pide
una nueva por DHCP. Por eso `red_oficina` tiene `ip_range: 192.168.10.96/27`:
Docker reparte `.96–.126` y deja libres `.50–.80` para el servidor DHCP.
Si no se separan los rangos, ambos podrían entregar la misma IP.

---

## 5. NAT: qué se demuestra

### 5.1 Teoría mínima

NAT traduce direcciones IP en el router. En el laboratorio se usan dos reglas:

**SNAT / MASQUERADE** (salida):

```text
pc_oficina 192.168.10.50:40000  --->  10.99.0.2:8000
                 |
                 | el router reescribe el origen
                 v
          10.99.0.254:50000      --->  10.99.0.2:8000
```

El servidor externo **nunca ve** la IP privada; ve la IP del router. Para
devolver la respuesta, el router guarda la traducción en la tabla de
*conntrack* (`10.99.0.254:50000 ↔ 192.168.10.50:40000`). Como muchas
conexiones comparten la IP pública, la distinción se hace por **puerto**:
eso es **PAT** (Port Address Translation), que es el NAT que usan los
routers domésticos.

**DNAT / port forwarding** (entrada):

```text
desde Internet: 10.99.0.254:8080  --->  192.168.10.50:8000
```

Permite publicar un servidor interno sin darle IP pública.

Detalles importantes:

- NAT no es un firewall: solo traduce. El filtrado lo hacen las reglas de
  `FORWARD`.
- Solo funciona si el router tiene **IP forwarding** activo.
- Las IP privadas (`10/8`, `172.16/12`, `192.168/16`, RFC 1918) no se enrutan
  en Internet; por eso hace falta NAT.

### 5.2 Ver las reglas

```bash
docker exec router_nat iptables -t nat -L POSTROUTING -v -n
docker exec router_nat iptables -t nat -L PREROUTING -v -n
```

```text
Chain POSTROUTING (policy ACCEPT 0 packets, 0 bytes)
 pkts bytes target     prot opt in     out     source               destination
    0     0 MASQUERADE  all  --  *      eth1    192.168.10.0/24      0.0.0.0/0

Chain PREROUTING (policy ACCEPT 0 packets, 0 bytes)
 pkts bytes target     prot opt in     out     source               destination
    0     0 DNAT       tcp  --  eth1   *       0.0.0.0/0            0.0.0.0/0       tcp dpt:8080 to:192.168.10.50:8000
```

Los contadores `pkts`/`bytes` son la prueba de que el tráfico pasó por la regla.

### 5.3 Probar la salida (SNAT)

```bash
docker exec pc_oficina curl -sS http://10.99.0.2:8000/
```

Devuelve la página "Servidor público". Para ver la traducción, capturar en el
lado externo mientras se genera el tráfico:

```bash
docker exec -d servidor_externo sh -c \
  "tcpdump -i eth0 -w /tmp/nat.pcap 'tcp port 8000'"

docker exec pc_oficina curl -sS -o /dev/null http://10.99.0.2:8000/
sleep 1
docker exec servidor_externo sh -c "pkill -INT tcpdump; sleep 1"
docker exec servidor_externo tcpdump -nn -r /tmp/nat.pcap
```

```text
IP 10.99.0.254.54321 > 10.99.0.2.8000: Flags [S], seq ...
#  ^^^^^^^^^^^ origen real: la IP del router, no 192.168.10.50
```

También se ve en el log del servidor externo (la IP que lo visitó) y en los
contadores de `MASQUERADE`. Todo junto:

```bash
python3 demo_redes.py traffic
```

### 5.4 Probar la entrada (DNAT)

Desde el servidor externo (como si fuéramos Internet):

```bash
docker exec servidor_externo curl -sS http://10.99.0.254:8080/
```

Devuelve la página **"Servidor interno"** (`192.168.10.50:8000`), que no tiene
IP pública: se llegó por el port forwarding del router.

```bash
python3 demo_redes.py dnat
```

### 5.5 Tabla de conexiones

```bash
docker exec router_nat cat /proc/net/nf_conntrack
```

Muestra las traducciones activas: IP/puerto privados ↔ IP/puerto públicos.

```bash
python3 demo_redes.py conntrack
```

---

## 6. Implementación propia del protocolo (Python)

Requisito de la consigna ("código que implemente o ejemplifique el
procedimiento paso a paso"). En `dhcp_py/` hay un cliente y un servidor DHCP
reales, **sin librerías externas**: solo `socket`, `struct` y `threading`.

| Archivo            | Qué hace                                                        |
|--------------------|-----------------------------------------------------------------|
| `dhcp_packet.py`   | Arma y parsea el mensaje DHCP (cabecera BOOTP + opciones TLV).  |
| `dhcp_server.py`   | Servidor: atiende DISCOVER/REQUEST/RELEASE/DECLINE/INFORM.      |
| `dhcp_client.py`   | Cliente: hace DORA y opcionalmente aplica la config con `ip`.   |
| `demo_dora.py`     | Corre ambos y muestra el intercambio intercalado en una consola.|

### 6.1 Sin Docker ni root (para probar en cualquier PC)

```bash
cd dhcp_py
python3 demo_dora.py
```

Usa `127.0.0.1` y puertos altos (`6767`/`6868`), así que **no necesita
permisos**. Salida (resumida):

```text
[ 0.512s] CLIENTE  | PASO 1/4  Enviando DHCPDISCOVER (broadcast, xid=0x49bc2e86)
[ 0.514s] SERVIDOR | DISCOVER  recibido de 02:42:ac:11:00:50 (xid=0x49bc2e86)
[ 0.514s] SERVIDOR | PASO 2/4  Enviando DHCPOFFER: te ofrezco 192.168.10.50
[ 0.515s] CLIENTE  | PASO 2/4  DHCPOFFER recibido: el servidor 127.0.0.1 ofrece 192.168.10.50
[ 0.515s] CLIENTE  | PASO 3/4  Enviando DHCPREQUEST: acepto 192.168.10.50 del servidor 127.0.0.1
[ 0.516s] SERVIDOR | PASO 4/4  Enviando DHCPACK: 192.168.10.50 queda asignada ... por 43200 s
[ 0.517s] CLIENTE  | PASO 4/4  DHCPACK recibido: concesion confirmada
[ 0.517s] CLIENTE  |           IP       : 192.168.10.50
[ 0.517s] CLIENTE  |           Mascara  : 255.255.255.0
[ 0.517s] CLIENTE  |           Gateway  : 192.168.10.254
[ 0.517s] CLIENTE  |           DNS      : 8.8.8.8, 1.1.1.1
```

Para ver los bytes de cada mensaje (cabecera BOOTP, cookie `0x63825363` y
opciones TLV):

```bash
python3 demo_dora.py --hexdump
```

### 6.2 En una red real, dentro de Docker

```bash
docker compose -f docker-compose.dhcp-py.yml up --build
```

Levanta un contenedor aislado en `192.168.50.0/24` y ejecuta `demo_dora.py` en
modo red: servidor y cliente usan los puertos **67/68 reales**, se comunican
por broadcast y el cliente **aplica** la IP recibida a su interfaz. Se ve con:

```bash
docker exec demo_dhcp_python ip -brief addr show eth0
```

### 6.3 Opciones útiles

```bash
# Servidor solo (puerto 67, requiere root):
python3 dhcp_server.py --interface eth0 \
    --range 192.168.10.50-192.168.10.80 \
    --router 192.168.10.254 --dns 8.8.8.8,1.1.1.1 \
    --server-id 192.168.10.2 --lease-file /tmp/mis.leases

# Cliente solo:
python3 dhcp_client.py --interface eth0 --configure --release
```

`--reserve MAC=IP` crea reservas fijas, igual que `--dhcp-host` de dnsmasq.
El servidor responde `DHCPNAK` si el cliente pide una IP reservada a otro,
fuera del rango o ya ocupada (se puede demostrar en clase).

---

## 7. `demo_redes.py`: la herramienta de la exposición

| Acción      | Qué hace                                                        |
|-------------|-----------------------------------------------------------------|
| `up`        | Construye y levanta el laboratorio; espera el DHCP              |
| `status`    | Estado de los contenedores e IPs                                |
| `dhcp`      | IP, ruta, concesiones y log del servidor                        |
| `dora`      | Captura tcpdump del intercambio DORA completo                   |
| `nat`       | Reglas y contadores de SNAT y DNAT                              |
| `traffic`   | Genera tráfico, lo captura y prueba la traducción               |
| `conntrack` | Tabla de conexiones NAT                                         |
| `dnat`      | Entra desde la red externa al servidor interno                  |
| `ping`      | Conectividad interna, externa e Internet                        |
| `down`      | Apaga y limpia                                                  |
| `all`       | Recorrido completo (up → … → ping)                              |

```bash
python3 demo_redes.py            # menú
python3 demo_redes.py dora       # una acción
python3 demo_redes.py all --sin-pausa
```

En Linux sin permisos de Docker, el script detecta y reintenta con `sudo`
(puede pedir la contraseña una vez).

---

## 8. Estructura del repositorio

```text
.
├── docker-compose.yml            # laboratorio principal (dnsmasq + NAT)
├── docker-compose.dhcp-py.yml    # implementación propia en una red real
├── demo_redes.py                 # demo interactiva / guion de la exposición
├── docker/
│   ├── Dockerfile                # imagen común (Alpine + herramientas)
│   ├── dhcp/entrypoint.sh        # servidor dnsmasq
│   ├── router/entrypoint.sh      # forwarding + MASQUERADE + DNAT
│   ├── pc/entrypoint.sh          # cliente udhcpc + web interno
│   ├── pc/udhcpc.script          # qué hace el cliente con el ACK
│   └── externo/entrypoint.sh     # servidor "de Internet"
├── dhcp_py/
│   ├── dhcp_packet.py            # mensaje DHCP (RFC 2131/2132)
│   ├── dhcp_server.py
│   ├── dhcp_client.py
│   └── demo_dora.py
└── web/
    ├── interno/index.html        # página del host privado
    └── externo/index.html        # página del host público
```

---

## 9. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| `permission denied ... docker.sock` | Agregar el usuario al grupo `docker` o usar `sudo`. |
| `iptables: can't initialize iptables table 'nat'` | El kernel no expone NAT al contenedor. En el host: `sudo modprobe nf_nat` y reiniciar Docker. |
| `pc_oficina` no tiene IP `.50` | Ver `docker logs pc_oficina`. Puede que el servidor no haya arrancado: `docker logs dhcp_server`. |
| El ping a `8.8.8.8` falla | La red de la facultad puede bloquear ICMP. No afecta al resto de la demo. |
| `curl` al puerto 8000 falla | Otro proceso del host usa ese puerto (no se publica, así que no debería). Revisar `docker logs servidor_externo`. |
| Scripts con error `bad interpreter: /bin/sh^M` | El repo se clonó con CRLF. Está resuelto en `.gitattributes`; si persiste: `git config core.autocrlf false` y volver a clonar. |
| Windows: los contenedores no levantan | Docker Desktop debe estar iniciado y con WSL2 habilitado. |
| `demo_redes.py` no ve Docker | Docker Desktop cerrado, o falta el grupo `docker` en Linux. |

---

## 10. Guion sugerido para la exposición (12–15 min)

| Min | Tema | Apoyo |
|----|------|-------|
| 0–1 | Problema: configurar la red a mano no escala | diapositivas |
| 1–3 | DHCP: DORA, opciones, lease, broadcast | diapositivas |
| 3–5 | Demo DHCP: IP, ruta y lease | `demo_redes.py dhcp` |
| 5–7 | Demo DORA capturado con tcpdump | `demo_redes.py dora` |
| 7–9 | Implementación propia: mostrar el código y correrlo | `dhcp_py/demo_dora.py --hexdump` |
| 9–11 | NAT: IP privada vs pública, SNAT/PAT, DNAT | diapositivas |
| 11–13 | Demo NAT: captura en el lado externo + contadores | `demo_redes.py traffic` |
| 13–14 | Demo DNAT: servidor interno desde afuera | `demo_redes.py dnat` |
| 14–15 | Límites de NAT, IPv6 y cierre | diapositivas |

Consejo: dejar el laboratorio ya levantado antes de empezar
(`docker compose up -d`) y usar `demo_redes.py` solo para ejecutar las acciones.

---

## 11. Preguntas típicas de defensa

**¿Por qué DISCOVER va en broadcast?**
Porque el cliente todavía no tiene IP ni conoce la del servidor; necesita que
cualquier servidor de la red lo escuche.

**¿Qué pasa si hay dos servidores DHCP?**
El cliente puede recibir varias ofertas y elige una (normalmente la primera);
luego envía `REQUEST` indicando en la opción 54 qué servidor eligió, y los
demás liberan la oferta.

**¿Cómo evita el servidor asignar dos veces la misma IP?**
Guarda las concesiones por MAC y verifica que la IP esté libre. dnsmasq además
hace un ping/ARP de comprobación antes de ofrecer.

**¿Qué es el lease y qué pasa al vencer?**
Es el tiempo por el que se presta la IP. Al 50% (T1) el cliente intenta
renovar con el mismo servidor; al 87.5% (T2) lo intenta con cualquiera; si no
renueva, al 100% la IP se libera.

**¿DHCP funciona entre subredes?**
No con broadcast. Se usa un **DHCP relay** (opción 82, `giaddr`) que reenvía
los mensajes al servidor.

**¿Qué diferencia hay entre NAT y PAT?**
NAT en sentido estricto traduce IPs una a una; PAT traduce también puertos,
permitiendo que muchos hosts compartan una IP. `MASQUERADE` es PAT con SNAT
automático de la IP de salida.

**¿NAT reemplaza a un firewall?**
No. NAT traduce; el filtrado lo hace el firewall. Además NAT rompe el modelo
extremo a extremo, lo que motivó IPv6.

**¿Por qué no se puede acceder al servidor interno desde afuera sin DNAT?**
Porque `192.168.10.50` es una IP privada (RFC 1918) que no se enruta en
Internet: el paquete no tiene camino de vuelta.

**¿Por qué el servidor externo ve la IP del router y no la del cliente?**
Porque `MASQUERADE` reescribe el origen antes de salir y guarda la traducción
en conntrack para devolver la respuesta.

**¿Qué pasa con las conexiones entrantes no solicitadas?**
Se descartan: no hay una entrada en conntrack que indique a qué host interno
entregarlas (salvo que exista una regla DNAT).

---

## 12. Referencias

- RFC 2131 — *Dynamic Host Configuration Protocol*
- RFC 2132 — *DHCP Options and BOOTP Vendor Extensions*
- RFC 3022 — *Traditional IP Network Address Translator (Traditional NAT)*
- RFC 1918 — *Address Allocation for Private Internets*
- `man dnsmasq`, `man udhcpc`, `man iptables-extensions`
