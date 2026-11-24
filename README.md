```markdown
# Proyecto de Redes: DHCP y NAT (Escenario de Pequeña Empresa)

Este proyecto simula una red interna de oficina (`192.168.10.0/24`) usando **Docker** y un script paso a paso en **Python** para explicar y defender **DHCP** y **NAT**.

## Requisitos previos
- Tener **Docker Desktop** instalado y abierto (con el motor/engine activo).
- Tener **Python 3** instalado en el equipo si van a ejecutar `demo_redes.py` directamente en la terminal.

## Cómo levantar el entorno
1. Abre una terminal (PowerShell, CMD o Bash) dentro de esta carpeta (`proyecto-dhcp-nat`).
2. Ejecuta:
   ```bash
   docker compose up -d

```

3. Verifica que los contenedores están activos:
```bash
docker ps

```



## Pruebas básicas de red

* Ver la IP de `pc_oficina`:
```bash
docker exec pc_oficina ip addr show eth0

```


* Ver la IP de `nodo_red`:
```bash
docker exec nodo_red ip addr show eth0

```


* Probar conectividad entre nodos:
```bash
docker exec pc_oficina ping -c 3 nodo_red

```



## Demostración paso a paso de DHCP y NAT (Script)

Ejecuta el script de demostración para mostrar el ciclo de asignación IP y la traducción de direcciones:

```bash
python demo_redes.py

```

## Cómo apagar y limpiar el entorno

Cuando terminen de probar:

```bash
docker compose down

```

```

### Archivo base `demo_redes.py` (Opcional)

```python
def simular_dhcp(mac_cliente, ip_ofrecida="192.168.10.50"):
    print("\n=== SIMULACIÓN PASO A PASO: DHCP ===")
    print(f"1. DISCOVER -> El cliente ({mac_cliente}) busca un servidor DHCP en la red (broadcast).")
    print(f"2. OFFER    -> El servidor DHCP ofrece la IP {ip_ofrecida}, máscara 255.255.255.0, gateway 192.168.10.1 y lease 3600s.")
    print(f"3. REQUEST  -> El cliente ({mac_cliente}) solicita formalmente usar la IP {ip_ofrecida}.")
    print(f"4. ACK      -> El servidor confirma y registra la concesión (lease) de {ip_ofrecida} para {mac_cliente}.")


def simular_nat(ip_interna, puerto_interno, ip_externa="200.50.10.5", puerto_externo=50001, destino="8.8.8.8:53"):
    print("\n=== SIMULACIÓN PASO A PASO: NAT (PAT) ===")
    tabla_nat = {
        "interna": f"{ip_interna}:{puerto_interno}",
        "externa": f"{ip_externa}:{puerto_externo}",
        "destino": destino,
    }
    print(f"1. SALIDA   -> {ip_interna}:{puerto_interno} envía tráfico hacia {destino}.")
    print(f"2. TRADUCCIÓN -> El router/gateway reemplaza {ip_interna}:{puerto_interno} por {ip_externa}:{puerto_externo}.")
    print(f"3. TABLA NAT -> {tabla_nat}")
    print(f"4. RETORNO  -> La respuesta llega a {ip_externa}:{puerto_externo}; NAT consulta la tabla y la envía a {ip_interna}:{puerto_interno}.")


if __name__ == "__main__":
    simular_dhcp("AA:BB:CC:11:22:33")
    simular_nat("192.168.10.50", 54321)

```