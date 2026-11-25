# Proyecto de Redes: DHCP y NAT

Este proyecto simula una red interna de oficina (`192.168.10.0/24`) usando **Docker** y un script paso a paso en **Python** (`demo_redes.py`) para explicar y defender el funcionamiento de **DHCP** y **NAT**.

## Requisitos previos

- Tener **Docker Desktop** instalado y abierto (con el motor/engine activo).
- Tener **Python 3** instalado en el equipo si van a ejecutar `demo_redes.py` directamente en la terminal.

## 1. Levantar y revisar el entorno en Docker

Abre una terminal (PowerShell, CMD o Bash) dentro de la carpeta del proyecto antes de ejecutar estos comandos.

- **Levantar los contenedores y la red en segundo plano:**

  ```bash
  docker compose up -d
  ```

  Crea la red de oficina `red_oficina` (`192.168.10.0/24`), la red de salida `red_externa` (`10.99.0.0/24`) e inicia los servicios `dhcp_server`, `router_nat`, `pc_oficina` y `nodo_red` según el `docker-compose.yml`.

  Direcciones fijas: `dhcp_server` = `192.168.10.2`, `nodo_red` = `192.168.10.3`, `router_nat` = `192.168.10.254`; `pc_oficina` obtiene la suya (`192.168.10.50`) por DHCP.

  **Resultado esperado:**

  ```text
  [+] Running 6/6
   ✔ Network dhcp-nat_red_oficina      Created
   ✔ Network dhcp-nat_red_externa      Created
   ✔ Container dhcp_server             Started
   ✔ Container router_nat              Started
   ✔ Container nodo_red                Started
   ✔ Container pc_oficina              Started
  ```

- **Ver qué contenedores están activos:**

  ```bash
  docker ps
  ```

  Muestra el listado de contenedores en ejecución, su estado (`Up`) y sus nombres para confirmar que todo está arriba.

  **Resultado esperado:**

  ```text
  CONTAINER ID   IMAGE           COMMAND                  PORTS   NAMES
  xxxxxxxxxxxx   alpine:latest   "/bin/sh -c sleep inf…"          nodo_red
  xxxxxxxxxxxx   alpine:latest   "/bin/sh -c sleep 6 &&…"         pc_oficina
  xxxxxxxxxxxx   alpine:latest   "/bin/sh -c apk add --n…"        router_nat
  xxxxxxxxxxxx   alpine:latest   "/bin/sh -c apk add --n…"        dhcp_server
  ```

## 2. Comandos de demostración: DHCP

Estos comandos permiten mostrar que `pc_oficina` obtiene su configuración de red automáticamente y que el servidor registra la concesión (*lease*).

- **Ver la IP y la MAC asignadas en `pc_oficina`:**

  ```bash
  docker exec pc_oficina ip addr show eth0
  ```

  Muestra la interfaz `eth0` del cliente, su dirección MAC y la dirección IPv4 asignada dentro del rango DHCP (`192.168.10.50`–`192.168.10.80`).

  **Resultado esperado:**

  ```text
  2: eth0@if41: <BROADCAST,MULTICAST,UP,LOWER_UP,M-DOWN> mtu 1500 qdisc noqueue state UP
      link/ether aa:f1:11:ff:62:8a brd ff:ff:ff:ff:ff:ff
      inet 192.168.10.72/24 brd 192.168.10.255 scope global eth0
         valid_lft forever preferred_lft forever
  ```

  > La IP exacta puede variar dentro del rango `.50`–`.80` según la MAC del cliente; la MAC mostrada es un ejemplo.

- **Ver la ruta por defecto (*gateway*) y rutas de red en `pc_oficina`:**

  ```bash
  docker exec pc_oficina ip route
  ```

  Comprueba que la puerta de enlace por defecto apunta a `router_nat` (`192.168.10.254`) y que existe la ruta hacia la subred `192.168.10.0/24`.

  **Resultado esperado:**

  ```text
  default via 192.168.10.254 dev eth0
  192.168.10.0/24 dev eth0 scope link src 192.168.10.72
  ```

- **Ver las concesiones (*leases*) entregadas por el servidor DHCP:**

  ```bash
  docker exec dhcp_server cat /tmp/dnsmasq.leases
  ```

  Muestra el archivo de concesiones de `dnsmasq` con el tiempo de expiración, la MAC del cliente, la IP asignada y el nombre del host como prueba del lado del servidor.

  **Resultado esperado:**

  ```text
  1795608678 aa:f1:11:ff:62:8a 192.168.10.72 * 01:aa:f1:11:ff:62:8a
  ```

  > Formato: `expira` `MAC` `IP` `nombre` `client-id`. Los valores variarán en cada ejecución.

- **Forzar manualmente una petición DHCP desde `pc_oficina`:**

  ```bash
  docker exec pc_oficina udhcpc -i eth0 -n -q
  ```

  Ejecuta el cliente DHCP ligero de Alpine para enviar peticiones (`Discover`/`Request`) y solicitar o renovar una concesión en `eth0`.

  **Resultado esperado:**

  ```text
  udhcpc: started, v1.36.1
  udhcpc: broadcasting discover
  udhcpc: broadcasting select for 192.168.10.72, server 192.168.10.2
  udhcpc: lease of 192.168.10.72 obtained from 192.168.10.2, lease time 43200
  ```

  > Nota: si la concesión actual sigue vigente, puede devolver directamente la misma IP sin mostrar todo el intercambio.

## 3. Comandos de demostración: NAT

Estos comandos permiten mostrar que el cliente tiene una dirección privada interna, que la regla NAT está configurada y que el tráfico logra salir y regresar a través de `router_nat`.

- **Confirmar que `pc_oficina` usa una IP privada interna:**

  ```bash
  docker exec pc_oficina ip addr show eth0
  ```

  Evidencia que el contenedor trabaja con una dirección privada (`192.168.10.x`) y no con una IP pública directa en su interfaz.

  **Resultado esperado:**

  ```text
  inet 192.168.10.72/24 brd 192.168.10.255 scope global eth0
  ```

- **Revisar la regla `MASQUERADE` en el contenedor `router_nat`:**

  ```bash
  docker exec router_nat iptables -t nat -L POSTROUTING -v -n
  ```

  Lista las reglas de la cadena `POSTROUTING` en la tabla `nat` de `router_nat`, mostrando la regla `MASQUERADE` para `192.168.10.0/24` y sus contadores de paquetes y bytes.

  **Resultado esperado:**

  ```text
  Chain POSTROUTING (policy ACCEPT 0 packets, 0 bytes)
   pkts bytes target     prot opt in     out     source               destination
      0     0 MASQUERADE  all  --  *      *       192.168.10.0/24      0.0.0.0/0
  ```

  > Se omite la fila interna `DOCKER_POSTROUTING` que Docker agrega para su DNS. Lo importante es que `MASQUERADE` empiece en `0` antes de generar tráfico externo.

- **Probar conectividad interna entre nodos de la oficina:**

  ```bash
  docker exec pc_oficina ping -c 3 nodo_red
  ```

  Envía 3 paquetes ICMP desde `pc_oficina` hacia `nodo_red` para verificar comunicación dentro de la red local sin pérdida de paquetes.

  **Resultado esperado:**

  ```text
  PING nodo_red (192.168.10.3): 56 data bytes
  64 bytes from 192.168.10.3: seq=0 ttl=64 time=0.080 ms
  64 bytes from 192.168.10.3: seq=1 ttl=64 time=0.072 ms
  64 bytes from 192.168.10.3: seq=2 ttl=64 time=0.075 ms

  --- nodo_red ping statistics ---
  3 packets transmitted, 3 packets received, 0% packet loss
  ```

- **Probar salida hacia una dirección externa:**

  ```bash
  docker exec pc_oficina ping -c 2 8.8.8.8
  ```

  Envía 2 paquetes a `8.8.8.8` desde un host con IP privada para comprobar que existe salida y retorno de respuesta a través de la traducción de direcciones que realiza `router_nat`.

  **Resultado esperado:**

  ```text
  PING 8.8.8.8 (8.8.8.8): 56 data bytes
  64 bytes from 8.8.8.8: seq=0 ttl=115 time=24.310 ms
  64 bytes from 8.8.8.8: seq=1 ttl=115 time=23.870 ms

  --- 8.8.8.8 ping statistics ---
  2 packets transmitted, 2 packets received, 0% packet loss
  ```

  > Nota: algunas redes bloquean ICMP a Internet. Si `8.8.8.8` no responde pero la red funciona, prueba con `docker exec pc_oficina ping -c 2 1.1.1.1`.

- **Comprobar que el tráfico externo pasó realmente por NAT:**

  ```bash
  docker exec router_nat iptables -t nat -L POSTROUTING -v -n
  ```

  Repite la revisión de la regla `MASQUERADE`: los contadores de paquetes y bytes deben haber aumentado respecto a la ejecución anterior.

  **Resultado esperado:**

  ```text
  Chain POSTROUTING (policy ACCEPT 0 packets, 0 bytes)
   pkts bytes target     prot opt in     out     source               destination
      2   168 MASQUERADE  all  --  *      *       192.168.10.0/24      0.0.0.0/0
  ```

  > Los números exactos pueden variar; lo relevante es que sean mayores que `0`, lo que demuestra que el tráfico externo de `pc_oficina` pasó por `router_nat`.

## 4. Apagar y limpiar el entorno

```bash
docker compose down
```

Detiene y elimina los contenedores y las redes creadas por Docker Compose para dejar el equipo limpio.

**Resultado esperado:**

```text
[+] Running 6/6
 ✔ Container pc_oficina              Removed
 ✔ Container nodo_red                Removed
 ✔ Container router_nat              Removed
 ✔ Container dhcp_server             Removed
 ✔ Network dhcp-nat_red_oficina      Removed
 ✔ Network dhcp-nat_red_externa      Removed
```